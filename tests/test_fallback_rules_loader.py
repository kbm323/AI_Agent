"""Comprehensive tests for the structured fallback rules YAML loader (Sub-AC 3.2.1).

Covers:
- Happy path: loading the real routing_rules.yaml and asserting
  structured object correctness
- Loading a minimal valid test fixture
- Loading with explicit path
- Default value population for optional fields
- Schema validation: invalid YAML files are rejected with
  appropriate exceptions
- Structured type guarantees: all attributes are the correct type
- Immutability: dataclass instances cannot be mutated after creation
- All sections are populated (teams, roles, agenda_types, risk_patterns,
  codex_triggers, priority_inference, guardrails, matching_algorithm,
  output_schema)
- Edge cases: empty defaults, missing optional fields, empty lists
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from src.fallback_rules_loader import (
    AgendaTypeSpec,
    CodexTrigger,
    Defaults,
    FallbackRules,
    Guardrail,
    MatchingAlgorithm,
    Metadata,
    ModelSpec,
    OutputSchema,
    PriorityRule,
    RiskPattern,
    RoleSpec,
    TeamSpec,
    load_fallback_rules,
)
from src.routing_rules_loader import load_routing_rules
from src.routing_rules_validator import (
    RoutingRulesValidationError,
    validate_routing_rules,
)


# ═══════════════════════════════════════════════════════════════════════════
# Fixture paths
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def fixtures_dir() -> Path:
    """Absolute path to the test fixtures directory."""
    return Path(__file__).resolve().parent / "fixtures" / "fallback_rules"


@pytest.fixture(scope="class")
def real_rules_path() -> Path:
    """Absolute path to the real project routing_rules.yaml."""
    return Path(__file__).resolve().parent.parent / "config" / "routing_rules.yaml"


@pytest.fixture
def minimal_valid_path(fixtures_dir: Path) -> Path:
    """Path to the minimal valid test fixture."""
    return fixtures_dir / "minimal_valid.yaml"


@pytest.fixture
def missing_sections_path(fixtures_dir: Path) -> Path:
    """Path to the missing-sections invalid fixture."""
    return fixtures_dir / "missing_sections.yaml"


@pytest.fixture
def bad_syntax_path(fixtures_dir: Path) -> Path:
    """Path to the bad YAML syntax fixture."""
    return fixtures_dir / "bad_syntax.yaml"


@pytest.fixture
def sequence_path(fixtures_dir: Path) -> Path:
    """Path to the non-mapping (sequence) YAML fixture."""
    return fixtures_dir / "sequence_not_mapping.yaml"


# ═══════════════════════════════════════════════════════════════════════════
# 1. Happy path — real routing_rules.yaml
# ═══════════════════════════════════════════════════════════════════════════


class TestRealRulesHappyPath:
    """Verify the loader correctly handles the project's real routing_rules.yaml."""

    @pytest.fixture(scope="class")
    def real_rules(self, real_rules_path: Path) -> FallbackRules:
        return FallbackRules.from_yaml_file(real_rules_path)

    def test_version(self, real_rules: FallbackRules) -> None:
        assert real_rules.version == "1.0.0"

    def test_metadata(self, real_rules: FallbackRules) -> None:
        md = real_rules.metadata
        assert isinstance(md, Metadata)
        assert "fallback" in md.description.lower()
        assert len(md.activated_when) >= 4
        assert md.primary_router == "qwen-llm-via-opencode-go"
        assert md.fallback_mode == "keyword_based_classification"

    def test_defaults(self, real_rules: FallbackRules) -> None:
        d = real_rules.defaults
        assert isinstance(d, Defaults)
        assert d.validator_required is True
        assert d.validator_model == "glm-5.1"
        assert d.codex_required is False
        assert d.max_roles_per_meeting == 7
        assert d.max_required_roles == 6
        assert d.quorum_minimum_ratio == 0.67

    def test_teams_count(self, real_rules: FallbackRules) -> None:
        assert len(real_rules.teams) == 6

    def test_teams_structure(self, real_rules: FallbackRules) -> None:
        for team_id, team in real_rules.teams.items():
            assert isinstance(team, TeamSpec)
            assert team.team_id == team_id
            assert team.name
            assert team.display_emoji
            assert team.description

    def test_roles_count(self, real_rules: FallbackRules) -> None:
        assert len(real_rules.roles) == 29

    def test_roles_structure(self, real_rules: FallbackRules) -> None:
        for role in real_rules.roles:
            assert isinstance(role, RoleSpec)
            assert role.role_id
            assert role.display_name
            assert role.team in real_rules.teams
            assert role.role_type in (
                "leader", "worker", "validator", "executor", "coordinator",
            )
            assert isinstance(role.model, ModelSpec)
            assert role.model.provider
            assert role.model.name
            assert isinstance(role.expertise_tags, tuple)

    def test_agenda_types_present(self, real_rules: FallbackRules) -> None:
        assert len(real_rules.agenda_types) >= 10
        for at in real_rules.agenda_types:
            assert isinstance(at, AgendaTypeSpec)
            assert at.id
            assert isinstance(at.keywords, tuple)

    def test_risk_patterns_present(self, real_rules: FallbackRules) -> None:
        assert len(real_rules.risk_patterns) >= 10
        for rp in real_rules.risk_patterns:
            assert isinstance(rp, RiskPattern)
            assert rp.risk_tag
            assert rp.severity in ("low", "medium", "high", "critical")

    def test_codex_triggers_present(self, real_rules: FallbackRules) -> None:
        assert len(real_rules.codex_triggers) == 7
        for ct in real_rules.codex_triggers:
            assert isinstance(ct, CodexTrigger)
            assert ct.id
            assert ct.trigger_number >= 1

    def test_priority_inference(self, real_rules: FallbackRules) -> None:
        assert len(real_rules.priority_inference) == 4
        for pr in real_rules.priority_inference:
            assert isinstance(pr, PriorityRule)
            assert pr.priority in ("P0", "P1", "P2", "P3")

    def test_default_priority(self, real_rules: FallbackRules) -> None:
        assert real_rules.default_priority == "P2"

    def test_guardrails(self, real_rules: FallbackRules) -> None:
        assert len(real_rules.guardrails) >= 10
        for g in real_rules.guardrails:
            assert isinstance(g, Guardrail)
            assert g.id
            assert g.enforcement

    def test_matching_algorithm(self, real_rules: FallbackRules) -> None:
        ma = real_rules.matching_algorithm
        assert isinstance(ma, MatchingAlgorithm)
        assert len(ma.steps) >= 5
        assert "korean" in ma.language_support

    def test_output_schema(self, real_rules: FallbackRules) -> None:
        os_ = real_rules.output_schema
        assert isinstance(os_, OutputSchema)
        assert "agenda_type" in os_.fields
        assert "priority" in os_.fields
        assert "codex_required" in os_.fields

    def test_raw_dict_access(self, real_rules: FallbackRules) -> None:
        """The raw validated dict is accessible for consumers that need it."""
        assert isinstance(real_rules.raw, dict)
        assert real_rules.raw["version"] == "1.0.0"


# ═══════════════════════════════════════════════════════════════════════════
# 2. Minimal valid fixture
# ═══════════════════════════════════════════════════════════════════════════


class TestMinimalRulesStructured:
    """Verify structured conversion of a minimal ruleset using from_dict.

    These tests bypass file-level validation (which requires 29 roles)
    and test the structured converter functions directly with
    pre-validated data or minimal data.
    """

    @pytest.fixture
    def minimal_data(self, minimal_valid_path: Path) -> dict:
        """Load the minimal YAML file as raw data (no validation)."""
        from src.routing_rules_loader import load_routing_rules
        return load_routing_rules(minimal_valid_path)

    def test_loads_successfully(self, minimal_data: dict) -> None:
        """The file itself loads without YAML errors."""
        assert minimal_data["version"] == "1.0.0"

    def test_from_dict_with_minimal_data(self, minimal_data: dict) -> None:
        """from_dict converts raw data to structured objects (no validation)."""
        rules = FallbackRules.from_dict(minimal_data)
        assert rules.version == "1.0.0"
        assert len(rules.roles) == 11  # Minimal fixture has 11 roles

    def test_teams_count(self, minimal_data: dict) -> None:
        rules = FallbackRules.from_dict(minimal_data)
        assert len(rules.teams) == 6

    def test_agenda_types_count(self, minimal_data: dict) -> None:
        rules = FallbackRules.from_dict(minimal_data)
        assert len(rules.agenda_types) == 3

    def test_risk_patterns(self, minimal_data: dict) -> None:
        rules = FallbackRules.from_dict(minimal_data)
        assert len(rules.risk_patterns) == 3

    def test_codex_triggers(self, minimal_data: dict) -> None:
        rules = FallbackRules.from_dict(minimal_data)
        assert len(rules.codex_triggers) == 2

    def test_guardrails(self, minimal_data: dict) -> None:
        rules = FallbackRules.from_dict(minimal_data)
        assert len(rules.guardrails) == 2

    def test_minimal_yaml_fails_validation(self, minimal_valid_path: Path) -> None:
        """The minimal fixture (11 roles) should FAIL schema validation
        because the validator requires exactly 29 roles."""
        from src.routing_rules_loader import load_routing_rules
        from src.routing_rules_validator import validate_routing_rules

        data = load_routing_rules(minimal_valid_path)
        with pytest.raises(RoutingRulesValidationError) as exc_info:
            validate_routing_rules(data)
        report = exc_info.value.report
        # Should flag wrong role count
        assert any(
            v.path == "roles" and v.error_type == "wrong_length"
            for v in report.violations
        )


# ═══════════════════════════════════════════════════════════════════════════
# 3. Default value population
# ═══════════════════════════════════════════════════════════════════════════


class TestDefaultValuePopulation:
    """Verify that missing optional fields receive correct default values."""

    def test_empty_defaults_section_fills_defaults(self) -> None:
        """When the defaults section is a valid dict but some fields
        are missing, they should be populated with canonical defaults."""
        from src.fallback_rules_loader import Defaults, _convert_defaults

        result = _convert_defaults({})
        assert result == Defaults()  # all defaults
        assert result.validator_required is True
        assert result.validator_model == "glm-5.1"
        assert result.max_roles_per_meeting == 7

    def test_partial_defaults_merge(self) -> None:
        """Partial defaults dict merges provided values with defaults."""
        from src.fallback_rules_loader import _convert_defaults

        result = _convert_defaults({
            "max_roles_per_meeting": 10,
            "validator_required": False,
        })
        assert result.max_roles_per_meeting == 10
        assert result.validator_required is False
        # Unspecified fields get defaults
        assert result.validator_model == "glm-5.1"
        assert result.codex_model == "gpt-5.5"
        assert result.max_required_roles == 6

    def test_role_without_expertise_tags_gets_empty_tuple(self) -> None:
        """Roles missing expertise_tags get an empty tuple, not None."""
        from src.fallback_rules_loader import _convert_roles

        roles = [{
            "role_id": "test-role",
            "display_name": "Test",
            "team": "validation",
            "role_type": "worker",
            "persistent_bot": False,
            "model": {"provider": "x", "name": "y", "fallback": "z"},
        }]
        result = _convert_roles(roles)
        assert len(result) == 1
        assert result[0].expertise_tags == ()

    def test_role_without_model_gets_empty_model(self) -> None:
        """Roles missing model dict get an empty ModelSpec with empty strings."""
        from src.fallback_rules_loader import _convert_roles

        roles = [{
            "role_id": "test-role",
            "display_name": "Test",
            "team": "validation",
            "role_type": "worker",
            "persistent_bot": False,
        }]
        result = _convert_roles(roles)
        assert len(result) == 1
        assert result[0].model.provider == ""
        assert result[0].model.name == ""
        assert result[0].model.fallback == ""

    def test_agenda_type_without_keywords_gets_empty_tuple(self) -> None:
        """Agenda type without keywords gets an empty tuple."""
        from src.fallback_rules_loader import _convert_agenda_types

        ats = [{"id": "test", "display_name": "Test"}]
        result = _convert_agenda_types(ats)
        assert result[0].keywords == ()

    def test_agenda_type_default_bool_values(self) -> None:
        """Agenda types default validator_required=True, codex_required=False."""
        from src.fallback_rules_loader import _convert_agenda_types

        ats = [{"id": "test", "display_name": "Test"}]
        result = _convert_agenda_types(ats)
        assert result[0].validator_required is True
        assert result[0].codex_required is False

    def test_risk_pattern_defaults(self) -> None:
        """Risk pattern with missing fields gets canonical defaults."""
        from src.fallback_rules_loader import _convert_risk_patterns

        rd = {"patterns": [{"risk_tag": "test_tag"}]}
        result = _convert_risk_patterns(rd)
        assert len(result) == 1
        assert result[0].risk_tag == "test_tag"
        assert result[0].severity == "medium"
        assert result[0].auto_codex is False
        assert result[0].requires_approval is False
        assert result[0].note == ""

    def test_codex_trigger_defaults(self) -> None:
        """Codex trigger with minimal fields gets defaults."""
        from src.fallback_rules_loader import _convert_codex_triggers

        er = {"codex_triggers": [{"id": "trigger-1"}]}
        result = _convert_codex_triggers(er)
        assert len(result) == 1
        assert result[0].id == "trigger-1"
        assert result[0].trigger_number == 0
        assert result[0].action == "set_codex_required_true"

    def test_guardrail_defaults(self) -> None:
        """Guardrail with only id gets empty string defaults."""
        from src.fallback_rules_loader import _convert_guardrails

        gr = [{"id": "test-guard"}]
        result = _convert_guardrails(gr)
        assert len(result) == 1
        assert result[0].id == "test-guard"
        assert result[0].description == ""
        assert result[0].rule == ""

    def test_priority_inference_defaults(self) -> None:
        """Priority rule with only id gets defaults."""
        from src.fallback_rules_loader import _convert_priority_inference

        pr = {"inference": [{"priority": "P0"}]}
        result = _convert_priority_inference(pr)
        assert len(result) == 1
        assert result[0].priority == "P0"
        assert result[0].label == ""
        assert result[0].keywords == ()

    def test_default_priority_fallback(self) -> None:
        """When no default is specified, returns 'P2'."""
        from src.fallback_rules_loader import _extract_default_priority

        assert _extract_default_priority({}) == "P2"
        assert _extract_default_priority({"default": "P1"}) == "P1"
        # Invalid default → fall back to P2
        assert _extract_default_priority({"default": "INVALID"}) == "P2"

    def test_matching_algorithm_defaults(self) -> None:
        """Empty matching_algorithm section returns all defaults."""
        from src.fallback_rules_loader import _convert_matching_algorithm

        ma = _convert_matching_algorithm({})
        assert ma.description == ""
        assert ma.steps == ()
        assert ma.language_support == ()

    def test_output_schema_defaults(self) -> None:
        """Empty output_schema section returns all defaults."""
        from src.fallback_rules_loader import _convert_output_schema

        os_ = _convert_output_schema({})
        assert os_.description == ""
        assert os_.fields == {}


# ═══════════════════════════════════════════════════════════════════════════
# 4. from_dict factory method
# ═══════════════════════════════════════════════════════════════════════════


class TestFromDict:
    """Verify FallbackRules.from_dict() correctly converts validated data."""

    def test_real_rules_via_from_dict(self, real_rules_path: Path) -> None:
        """Loading via from_yaml_file and from_dict should produce the
        same structured result (minus raw which is not compared)."""
        data = load_routing_rules(real_rules_path)
        validated = validate_routing_rules(data)
        rules_from_dict = FallbackRules.from_dict(validated)
        rules_from_file = FallbackRules.from_yaml_file(real_rules_path)

        # Exclude raw (compare=False) and check all fields match
        assert rules_from_dict.version == rules_from_file.version
        assert rules_from_dict.metadata == rules_from_file.metadata
        assert rules_from_dict.defaults == rules_from_file.defaults
        assert rules_from_dict.teams == rules_from_file.teams
        assert rules_from_dict.roles == rules_from_file.roles
        assert rules_from_dict.agenda_types == rules_from_file.agenda_types
        assert rules_from_dict.risk_patterns == rules_from_file.risk_patterns
        assert rules_from_dict.codex_triggers == rules_from_file.codex_triggers
        assert rules_from_dict.priority_inference == rules_from_file.priority_inference
        assert rules_from_dict.default_priority == rules_from_file.default_priority
        assert rules_from_dict.guardrails == rules_from_file.guardrails
        assert rules_from_dict.matching_algorithm == rules_from_file.matching_algorithm
        assert rules_from_dict.output_schema == rules_from_file.output_schema

    def test_from_dict_rejects_non_dict(self) -> None:
        """from_dict raises TypeError for non-dict input."""
        with pytest.raises(TypeError, match="must be a dict"):
            FallbackRules.from_dict([])  # type: ignore[arg-type]

    def test_from_dict_minimal_data(self) -> None:
        """from_dict should work with an almost-empty dict (defaults everywhere)."""
        from src.routing_rules_validator import validate_routing_rules

        # Build a complete minimal schema-valid dict
        minimal = load_routing_rules(
            Path(__file__).resolve().parent
            / "fixtures" / "fallback_rules" / "minimal_valid.yaml"
        )
        rules = FallbackRules.from_dict(minimal)
        assert rules.version == "1.0.0"
        assert len(rules.roles) == 11


# ═══════════════════════════════════════════════════════════════════════════
# 5. from_yaml_file error handling
# ═══════════════════════════════════════════════════════════════════════════


class TestFromYamlFileErrors:
    """Verify proper error propagation from the loader pipeline."""

    def test_file_not_found(self, tmp_path: Path) -> None:
        nonexistent = tmp_path / "nonexistent.yaml"
        with pytest.raises(FileNotFoundError):
            FallbackRules.from_yaml_file(nonexistent)

    def test_bad_syntax_raises_yaml_error(self, bad_syntax_path: Path) -> None:
        with pytest.raises(yaml.YAMLError):
            FallbackRules.from_yaml_file(bad_syntax_path)

    def test_sequence_raises_yaml_error(self, sequence_path: Path) -> None:
        with pytest.raises((yaml.YAMLError, RoutingRulesValidationError)):
            FallbackRules.from_yaml_file(sequence_path)

    def test_missing_sections_raises_validation_error(
        self, missing_sections_path: Path
    ) -> None:
        with pytest.raises(RoutingRulesValidationError) as exc_info:
            FallbackRules.from_yaml_file(missing_sections_path)
        report = exc_info.value.report
        assert not report.passed
        # Should flag version and metadata as missing
        missing = {
            v.path.split(".")[0] for v in report.violations
            if v.error_type == "missing_section"
        }
        assert "version" in missing
        assert "metadata" in missing


# ═══════════════════════════════════════════════════════════════════════════
# 6. Structured type correctness
# ═══════════════════════════════════════════════════════════════════════════


class TestStructuredTypes:
    """Verify every attribute is of the expected type.

    Uses from_dict with minimal data to bypass the 29-role validation
    requirement while still exercising all type conversions.
    """

    @pytest.fixture
    def rules(self, minimal_valid_path: Path) -> FallbackRules:
        from src.routing_rules_loader import load_routing_rules
        data = load_routing_rules(minimal_valid_path)
        return FallbackRules.from_dict(data)

    def test_version_is_str(self, rules: FallbackRules) -> None:
        assert isinstance(rules.version, str)

    def test_metadata_type(self, rules: FallbackRules) -> None:
        assert isinstance(rules.metadata, Metadata)

    def test_defaults_type(self, rules: FallbackRules) -> None:
        assert isinstance(rules.defaults, Defaults)

    def test_teams_value_types(self, rules: FallbackRules) -> None:
        for team in rules.teams.values():
            assert isinstance(team, TeamSpec)

    def test_roles_are_tuple_of_role_spec(self, rules: FallbackRules) -> None:
        assert isinstance(rules.roles, tuple)
        for role in rules.roles:
            assert isinstance(role, RoleSpec)
            assert isinstance(role.model, ModelSpec)

    def test_agenda_types_are_tuple(self, rules: FallbackRules) -> None:
        assert isinstance(rules.agenda_types, tuple)
        for at in rules.agenda_types:
            assert isinstance(at, AgendaTypeSpec)

    def test_risk_patterns_are_tuple(self, rules: FallbackRules) -> None:
        assert isinstance(rules.risk_patterns, tuple)
        for rp in rules.risk_patterns:
            assert isinstance(rp, RiskPattern)

    def test_codex_triggers_are_tuple(self, rules: FallbackRules) -> None:
        assert isinstance(rules.codex_triggers, tuple)
        for ct in rules.codex_triggers:
            assert isinstance(ct, CodexTrigger)

    def test_priority_inference_is_tuple(self, rules: FallbackRules) -> None:
        assert isinstance(rules.priority_inference, tuple)
        for pr in rules.priority_inference:
            assert isinstance(pr, PriorityRule)

    def test_guardrails_are_tuple(self, rules: FallbackRules) -> None:
        assert isinstance(rules.guardrails, tuple)
        for g in rules.guardrails:
            assert isinstance(g, Guardrail)

    def test_matching_algorithm_type(self, rules: FallbackRules) -> None:
        assert isinstance(rules.matching_algorithm, MatchingAlgorithm)

    def test_output_schema_type(self, rules: FallbackRules) -> None:
        assert isinstance(rules.output_schema, OutputSchema)


# ═══════════════════════════════════════════════════════════════════════════
# 7. Immutability
# ═══════════════════════════════════════════════════════════════════════════


class TestImmutability:
    """FallbackRules and all sub-dataclasses are frozen (immutable).

    Uses from_dict with minimal data to bypass the 29-role validation
    requirement while still exercising immutability checks.
    """

    @pytest.fixture
    def rules(self, minimal_valid_path: Path) -> FallbackRules:
        from src.routing_rules_loader import load_routing_rules
        data = load_routing_rules(minimal_valid_path)
        return FallbackRules.from_dict(data)

    def test_fallback_rules_is_immutable(self, rules: FallbackRules) -> None:
        with pytest.raises(Exception):
            rules.version = "2.0.0"  # type: ignore[misc]

    def test_role_spec_is_immutable(self, rules: FallbackRules) -> None:
        role = rules.roles[0]
        with pytest.raises(Exception):
            role.role_id = "new-id"  # type: ignore[misc]

    def test_model_spec_is_immutable(self, rules: FallbackRules) -> None:
        model = rules.roles[0].model
        with pytest.raises(Exception):
            model.name = "new-model"  # type: ignore[misc]

    def test_defaults_is_immutable(self, rules: FallbackRules) -> None:
        with pytest.raises(Exception):
            rules.defaults.max_roles_per_meeting = 99  # type: ignore[misc]

    def test_risk_pattern_is_immutable(self, rules: FallbackRules) -> None:
        rp = rules.risk_patterns[0]
        with pytest.raises(Exception):
            rp.severity = "critical"  # type: ignore[misc]

    def test_agenda_type_is_immutable(self, rules: FallbackRules) -> None:
        at = rules.agenda_types[0]
        with pytest.raises(Exception):
            at.codex_required = True  # type: ignore[misc]


# ═══════════════════════════════════════════════════════════════════════════
# 8. Convenience function
# ═══════════════════════════════════════════════════════════════════════════


class TestLoadFallbackRules:
    """Verify the load_fallback_rules convenience function."""

    def test_loads_real_rules(self) -> None:
        """Default path should find the real config file."""
        rules = load_fallback_rules()
        assert rules.version == "1.0.0"
        assert len(rules.roles) == 29

    def test_loads_explicit_path(self, real_rules_path: Path) -> None:
        """Explicit path to the real rules file works."""
        rules = load_fallback_rules(real_rules_path)
        assert len(rules.roles) == 29

    def test_loads_string_path(self, real_rules_path: Path) -> None:
        """String path to the real rules file works."""
        rules = load_fallback_rules(str(real_rules_path))
        assert len(rules.roles) == 29


# ═══════════════════════════════════════════════════════════════════════════
# 9. Round-trip consistency
# ═══════════════════════════════════════════════════════════════════════════


class TestRoundTrip:
    """Verify that data survives the YAML → struct → attr read path intact."""

    @pytest.fixture
    def rules(self, real_rules_path: Path) -> FallbackRules:
        return FallbackRules.from_yaml_file(real_rules_path)

    def test_team_ids_match_dict_keys(self, rules: FallbackRules) -> None:
        for team_id, team in rules.teams.items():
            assert team.team_id == team_id

    def test_role_teams_exist(self, rules: FallbackRules) -> None:
        for role in rules.roles:
            assert role.team in rules.teams, (
                f"Role {role.role_id} references unknown team {role.team}"
            )

    def test_agenda_type_roles_exist(self, rules: FallbackRules) -> None:
        all_role_ids = {r.role_id for r in rules.roles}
        for at in rules.agenda_types:
            for rid in at.required_roles:
                assert rid in all_role_ids, (
                    f"Agenda type {at.id} requires unknown role {rid}"
                )

    def test_codex_trigger_numbers_are_sequential(self, rules: FallbackRules) -> None:
        numbers = [ct.trigger_number for ct in rules.codex_triggers]
        assert sorted(numbers) == list(range(1, 8))


# ═══════════════════════════════════════════════════════════════════════════
# 10. Edge cases
# ═══════════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    """Miscellaneous edge case coverage."""

    def test_empty_file_validation_rejected(self, tmp_path: Path) -> None:
        """An empty YAML file fails schema validation."""
        f = tmp_path / "empty.yaml"
        f.write_text("", encoding="utf-8")
        with pytest.raises(RoutingRulesValidationError):
            FallbackRules.from_yaml_file(f)

    def test_comments_only_file_validation_rejected(self, tmp_path: Path) -> None:
        """Comments-only YAML file fails schema validation."""
        f = tmp_path / "comments.yaml"
        f.write_text("# just comments\n# nothing here\n", encoding="utf-8")
        with pytest.raises(RoutingRulesValidationError):
            FallbackRules.from_yaml_file(f)

    def test_persistent_bot_without_discord_name(self) -> None:
        """Role marked persistent_bot without discord_name gets empty string."""
        from src.fallback_rules_loader import _convert_roles

        roles = [{
            "role_id": "test-role",
            "display_name": "Test",
            "team": "validation",
            "role_type": "leader",
            "persistent_bot": True,
            "model": {"provider": "x", "name": "y", "fallback": "z"},
        }]
        result = _convert_roles(roles)
        assert result[0].discord_name == ""

    def test_non_bool_persistent_bot_coerced(self) -> None:
        """Non-boolean persistent_bot values are coerced via bool()."""
        from src.fallback_rules_loader import _convert_roles

        roles = [{
            "role_id": "test-role",
            "display_name": "Test",
            "team": "validation",
            "role_type": "validator",
            "persistent_bot": 1,  # truthy int → True
            "model": {"provider": "x", "name": "y", "fallback": "z"},
        }]
        result = _convert_roles(roles)
        assert result[0].persistent_bot is True

    def test_safe_str_tuple_with_none(self) -> None:
        """_safe_str_tuple(None) returns empty tuple."""
        from src.fallback_rules_loader import _safe_str_tuple

        assert _safe_str_tuple(None) == ()
        assert _safe_str_tuple("not_a_list") == ()
        assert _safe_str_tuple(42) == ()

    def test_safe_str_tuple_with_valid_list(self) -> None:
        from src.fallback_rules_loader import _safe_str_tuple

        assert _safe_str_tuple(["a", "b", "c"]) == ("a", "b", "c")
        assert _safe_str_tuple(("x", "y")) == ("x", "y")

    def test_team_spec_non_dict_entry_skipped(self) -> None:
        """Non-dict team entries are silently skipped."""
        from src.fallback_rules_loader import _convert_teams

        teams = {
            "valid-team": {"name": "Valid", "display_emoji": "V", "description": "ok"},
            "bad-team": "not_a_dict",
        }
        result = _convert_teams(teams)
        assert "valid-team" in result
        assert "bad-team" not in result

    def test_role_non_dict_entry_skipped(self) -> None:
        """Non-dict role entries are silently skipped."""
        from src.fallback_rules_loader import _convert_roles

        roles = [
            {"role_id": "role-1", "display_name": "R1", "team": "validation",
             "role_type": "worker", "persistent_bot": False,
             "model": {"provider": "x", "name": "y", "fallback": "z"}},
            "not_a_dict",
        ]
        result = _convert_roles(roles)
        assert len(result) == 1
        assert result[0].role_id == "role-1"
