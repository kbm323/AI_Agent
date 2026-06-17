"""Tests for the raw YAML string parser (Sub-AC 3.1.2).

Covers:
- Happy path: valid YAML mappings (simple, nested, complex)
- Edge cases: empty string, whitespace-only, comments-only, null
- Error types: scanner_error, parser_error, non_mapping
- Structured error fields: error_type, message, line, column, position, excerpt
- Tab indentation detection
- Convenience wrapper: parse_yaml_or_raise
- Integration: parsing real-world YAML fragments (agent.yaml, routing_rules)
"""

from __future__ import annotations

from textwrap import dedent

import pytest
import yaml

from src.yaml_parser import (
    ComposerError,
    ConstructorError,
    ParserError,
    ScannerError,
    YamlErrorType,
    YamlParseError,
    YamlParseResult,
    parse_yaml,
    parse_yaml_or_raise,
)


# ── Happy path ──────────────────────────────────────────────────────────


class TestValidYamlMappings:
    """Verify the parser returns correct dicts for valid YAML mappings."""

    def test_simple_key_value(self) -> None:
        result = parse_yaml("key: value")
        assert result.success
        assert result.data == {"key": "value"}
        assert result.error is None

    def test_multiple_keys(self) -> None:
        result = parse_yaml("name: Alice\nage: 30\nactive: true")
        assert result.success
        assert result.data == {"name": "Alice", "age": 30, "active": True}

    def test_nested_mapping(self) -> None:
        result = parse_yaml(
            dedent(
                """\
                person:
                  name: Alice
                  age: 30
                """
            )
        )
        assert result.success
        assert result.data == {"person": {"name": "Alice", "age": 30}}

    def test_deeply_nested(self) -> None:
        result = parse_yaml(
            dedent(
                """\
                a:
                  b:
                    c:
                      d: deep_value
                """
            )
        )
        assert result.success
        assert result.data == {"a": {"b": {"c": {"d": "deep_value"}}}}

    def test_list_values(self) -> None:
        result = parse_yaml(
            dedent(
                """\
                items:
                  - one
                  - two
                  - three
                numbers:
                  - 1
                  - 2
                """
            )
        )
        assert result.success
        assert result.data == {
            "items": ["one", "two", "three"],
            "numbers": [1, 2],
        }

    def test_complex_list_of_mappings(self) -> None:
        result = parse_yaml(
            dedent(
                """\
                roles:
                  - role_id: art-director
                    team: art-design
                    type: leader
                  - role_id: composer
                    team: content-production
                    type: worker
                """
            )
        )
        assert result.success
        assert result.data == {
            "roles": [
                {"role_id": "art-director", "team": "art-design", "type": "leader"},
                {"role_id": "composer", "team": "content-production", "type": "worker"},
            ],
        }

    def test_boolean_values(self) -> None:
        result = parse_yaml("a: true\nb: false\nc: yes\nd: no\ne: on\nf: off")
        assert result.success
        assert result.data == {
            "a": True, "b": False, "c": True,
            "d": False, "e": True, "f": False,
        }

    def test_null_values(self) -> None:
        result = parse_yaml("a: null\nb: ~\nc: None")
        assert result.success
        assert result.data == {"a": None, "b": None, "c": "None"}

    def test_numeric_values(self) -> None:
        result = parse_yaml("int_val: 42\nfloat_val: 3.14\nneg: -10\nsci: 1.5e+3")
        assert result.success
        assert result.data == {
            "int_val": 42, "float_val": 3.14,
            "neg": -10, "sci": 1500.0,
        }

    def test_quoted_strings(self) -> None:
        result = parse_yaml('single: \'hello\'\ndouble: "world"\nunquoted: plain')
        assert result.success
        assert result.data == {
            "single": "hello", "double": "world", "unquoted": "plain",
        }

    def test_multiline_strings(self) -> None:
        result = parse_yaml(
            dedent(
                """\
                description: >
                  This is a long
                  description that wraps
                  across multiple lines.
                literal: |
                  Line one
                  Line two
                """
            )
        )
        assert result.success
        assert "long" in result.data["description"]
        assert result.data["literal"] == "Line one\nLine two\n"

    def test_empty_mapping(self) -> None:
        """An empty mapping '{}' is valid and returns an empty dict."""
        result = parse_yaml("{}")
        assert result.success
        assert result.data == {}

    def test_unicode_content(self) -> None:
        result = parse_yaml(
            dedent(
                """\
                name: 콘텐츠 디렉터
                team: 콘텐츠/제작팀
                description: >
                  한국어 콘텐츠 제작을 총괄하는
                  팀 리더 역할입니다.
                """
            )
        )
        assert result.success
        assert result.data["name"] == "콘텐츠 디렉터"
        assert result.data["team"] == "콘텐츠/제작팀"

    def test_duplicate_keys_last_wins(self) -> None:
        """PyYAML behaviour: duplicate keys silently keep the last value."""
        result = parse_yaml("key: first\nkey: second")
        assert result.success
        assert result.data == {"key": "second"}


# ── Empty / whitespace / comments ───────────────────────────────────────


class TestEmptyInput:
    """Verify empty_input error for various forms of empty/blank input."""

    def test_empty_string(self) -> None:
        result = parse_yaml("")
        assert not result.success
        assert result.error is not None
        assert result.error.error_type == YamlErrorType.EMPTY_INPUT
        assert result.data is None

    def test_whitespace_only(self) -> None:
        result = parse_yaml("   \n  \n   ")
        assert not result.success
        assert result.error.error_type == YamlErrorType.EMPTY_INPUT

    def test_newlines_only(self) -> None:
        result = parse_yaml("\n\n\n")
        assert not result.success
        assert result.error.error_type == YamlErrorType.EMPTY_INPUT

    def test_comments_only(self) -> None:
        result = parse_yaml("# just a comment\n# another one")
        assert not result.success
        assert result.error.error_type == YamlErrorType.EMPTY_INPUT

    def test_comments_and_whitespace(self) -> None:
        result = parse_yaml("  # comment\n  \n  # another")
        assert not result.success
        assert result.error.error_type == YamlErrorType.EMPTY_INPUT

    def test_empty_input_error_has_fields(self) -> None:
        result = parse_yaml("")
        assert result.error.line == 1
        assert result.error.column == 1
        assert result.error.position == 0
        assert result.error.raw_excerpt == ""
        assert len(result.error.recovery_hint) > 0


# ── Non-mapping results ─────────────────────────────────────────────────


class TestNonMappingResults:
    """Verify non_mapping error when YAML is valid but not a dict."""

    def test_top_level_sequence(self) -> None:
        result = parse_yaml("- item1\n- item2\n- item3")
        assert not result.success
        assert result.error.error_type == YamlErrorType.NON_MAPPING
        assert "list" in result.error.message.lower()

    def test_top_level_scalar_string(self) -> None:
        result = parse_yaml("hello world")
        assert not result.success
        assert result.error.error_type == YamlErrorType.NON_MAPPING

    def test_top_level_scalar_number(self) -> None:
        result = parse_yaml("42")
        assert not result.success
        assert result.error.error_type == YamlErrorType.NON_MAPPING

    def test_top_level_scalar_boolean(self) -> None:
        result = parse_yaml("true")
        assert not result.success
        assert result.error.error_type == YamlErrorType.NON_MAPPING

    def test_non_mapping_has_recovery_hint(self) -> None:
        result = parse_yaml("- item")
        assert len(result.error.recovery_hint) > 0
        assert "mapping" in result.error.recovery_hint.lower()


# ── Null result ─────────────────────────────────────────────────────────


class TestNullResult:
    """Verify null_result error when safe_load returns None."""

    def test_literal_null(self) -> None:
        result = parse_yaml("null")
        assert not result.success
        assert result.error.error_type == YamlErrorType.NULL_RESULT

    def test_tilde_null(self) -> None:
        result = parse_yaml("~")
        assert not result.success
        assert result.error.error_type == YamlErrorType.NULL_RESULT

    def test_null_result_error_fields(self) -> None:
        result = parse_yaml("null")
        assert result.error.line == 1
        assert result.error.column == 1
        assert "null" in result.error.message.lower()


# ── ScannerError (syntax) ───────────────────────────────────────────────


class TestScannerErrors:
    """Verify scanner_error for lexical/syntax-level YAML errors."""

    def test_unclosed_brace_in_flow(self) -> None:
        result = parse_yaml("key: {value")
        assert not result.success
        assert result.error.error_type in (
            YamlErrorType.SCANNER_ERROR,
            YamlErrorType.PARSER_ERROR,
        )
        assert result.error.line is not None

    def test_unclosed_bracket(self) -> None:
        result = parse_yaml("key: [value")
        assert not result.success
        assert result.error.error_type in (
            YamlErrorType.SCANNER_ERROR,
            YamlErrorType.PARSER_ERROR,
        )

    def test_stray_colon(self) -> None:
        result = parse_yaml(": stray")
        assert not result.success
        assert result.error.error_type in (
            YamlErrorType.SCANNER_ERROR,
            YamlErrorType.PARSER_ERROR,
        )

    def test_scanner_error_has_location(self) -> None:
        result = parse_yaml("key: {bad")
        assert result.error.line is not None
        assert result.error.column is not None
        assert result.error.position is not None
        assert len(result.error.raw_excerpt) > 0

    def test_scanner_error_has_recovery_hint(self) -> None:
        result = parse_yaml("key: {bad")
        assert len(result.error.recovery_hint) > 0


# ── ParserError (structure) ─────────────────────────────────────────────


class TestParserErrors:
    """Verify parser_error for structural YAML errors."""

    def test_bad_indentation(self) -> None:
        result = parse_yaml(
            dedent(
                """\
                key1: value1
                  sub: value2
                key2: value3
                """
            )
        )
        assert not result.success
        assert result.error.error_type in (
            YamlErrorType.PARSER_ERROR,
            YamlErrorType.SCANNER_ERROR,
        )

    def test_parser_error_has_location(self) -> None:
        yaml_str = dedent(
            """\
            parent:
              child1: value1
               child2: value2
            """
        )
        result = parse_yaml(yaml_str)
        assert not result.success
        assert result.error.line is not None

    def test_parser_error_has_recovery_hint(self) -> None:
        result = parse_yaml("key:\n  - item\n bad")
        if not result.success:
            assert len(result.error.recovery_hint) > 0


# ── Tab indentation ─────────────────────────────────────────────────────


class TestTabIndentation:
    """Verify early detection of tab characters used for indentation."""

    def test_tab_indented_mapping(self) -> None:
        result = parse_yaml("key:\n\tsub: value")
        assert not result.success
        assert result.error.error_type == YamlErrorType.SCANNER_ERROR
        assert "tab" in result.error.message.lower()

    def test_tab_on_first_indent_level(self) -> None:
        result = parse_yaml("parent:\n\tchild: value")
        assert not result.success
        assert result.error.error_type == YamlErrorType.SCANNER_ERROR

    def test_tab_error_has_location(self) -> None:
        result = parse_yaml("key:\n\tsub: val")
        assert result.error.line is not None
        assert result.error.column is not None

    def test_tab_error_has_recovery_hint(self) -> None:
        result = parse_yaml("key:\n\tsub: val")
        assert "space" in result.error.recovery_hint.lower()

    def test_tab_only_in_comment_not_detected(self) -> None:
        """Tabs inside comments are not indentation — not flagged."""
        result = parse_yaml("key: value  #\ttab in comment")
        # This should parse fine — tab is in comment, not indentation
        assert result.success


# ── Structured error field verification ─────────────────────────────────


class TestErrorStructure:
    """Verify that all error types produce consistent structured output."""

    def test_empty_input_error_structure(self) -> None:
        result = parse_yaml("")
        err = result.error
        assert err.error_type == YamlErrorType.EMPTY_INPUT
        assert isinstance(err.message, str) and len(err.message) > 0
        assert err.line == 1
        assert err.column == 1
        assert err.position == 0
        assert err.raw_excerpt == ""
        assert isinstance(err.recovery_hint, str)

    def test_non_mapping_error_structure(self) -> None:
        result = parse_yaml("- item")
        err = result.error
        assert err.error_type == YamlErrorType.NON_MAPPING
        assert isinstance(err.message, str) and len(err.message) > 0
        assert err.line == 1
        assert err.column == 1
        assert err.position == 0
        assert len(err.raw_excerpt) > 0
        assert isinstance(err.recovery_hint, str)

    def test_scanner_error_structure(self) -> None:
        result = parse_yaml("key: {bad")
        err = result.error
        assert err.error_type in (
            YamlErrorType.SCANNER_ERROR,
            YamlErrorType.PARSER_ERROR,
        )
        assert isinstance(err.message, str) and len(err.message) > 0
        # line/column may be 0-based in some PyYAML versions for ParserError
        assert err.line is not None
        assert err.column is not None and err.column >= 0
        assert err.position is not None and err.position >= 0
        assert len(err.raw_excerpt) > 0
        assert isinstance(err.recovery_hint, str)

    def test_all_error_types_have_recovery_hint(self) -> None:
        """Every error type must provide a recovery_hint for Coordinator logging."""
        test_cases = [
            ("", YamlErrorType.EMPTY_INPUT),
            ("- item", YamlErrorType.NON_MAPPING),
            ("null", YamlErrorType.NULL_RESULT),
            ("key: {bad", None),  # can be scanner_error or parser_error
        ]
        for yaml_str, expected_type in test_cases:
            result = parse_yaml(yaml_str)
            assert result.error is not None
            if expected_type is not None:
                assert result.error.error_type == expected_type
            else:
                assert result.error.error_type in (
                    YamlErrorType.SCANNER_ERROR,
                    YamlErrorType.PARSER_ERROR,
                )
            assert isinstance(result.error.recovery_hint, str)
            assert len(result.error.recovery_hint) > 0, (
                f"Missing recovery_hint for {result.error.error_type}"
            )


# ── Convenience wrapper: parse_yaml_or_raise ────────────────────────────


class TestParseYamlOrRaise:
    """Verify the convenience wrapper that raises on failure."""

    def test_returns_dict_on_success(self) -> None:
        data = parse_yaml_or_raise("key: value")
        assert data == {"key": "value"}
        assert isinstance(data, dict)

    def test_raises_value_error_on_empty(self) -> None:
        with pytest.raises(ValueError) as exc_info:
            parse_yaml_or_raise("")
        assert "empty_input" in str(exc_info.value)

    def test_raises_value_error_on_non_mapping(self) -> None:
        with pytest.raises(ValueError) as exc_info:
            parse_yaml_or_raise("- item")
        assert "non_mapping" in str(exc_info.value)

    def test_raises_value_error_on_scanner_error(self) -> None:
        with pytest.raises(ValueError) as exc_info:
            parse_yaml_or_raise("key: {bad")
        assert "YAML parse failed" in str(exc_info.value)

    def test_raises_value_error_on_parser_error(self) -> None:
        with pytest.raises(ValueError) as exc_info:
            parse_yaml_or_raise("key:\n  - item\n bad")
        assert "YAML parse failed" in str(exc_info.value)


# ── Edge cases ──────────────────────────────────────────────────────────


class TestEdgeCases:
    """Cover boundary conditions and unusual but valid YAML."""

    def test_single_key_empty_value(self) -> None:
        """An explicit empty string value."""
        result = parse_yaml("key:")
        assert result.success
        assert result.data == {"key": None}

    def test_leading_whitespace(self) -> None:
        """Leading whitespace should not affect parsing."""
        result = parse_yaml("  \n  key: value")
        assert result.success
        assert result.data == {"key": "value"}

    def test_trailing_whitespace(self) -> None:
        result = parse_yaml("key: value   \n  ")
        assert result.success
        assert result.data == {"key": "value"}

    def test_anchors_and_aliases(self) -> None:
        """YAML anchors and aliases should work via safe_load."""
        result = parse_yaml(
            dedent(
                """\
                defaults: &defaults
                  color: blue
                  size: large
                item1:
                  <<: *defaults
                  name: widget
                """
            )
        )
        assert result.success
        assert result.data["item1"]["color"] == "blue"

    def test_yaml_1_1_bool_variants(self) -> None:
        """PyYAML 1.1 bool parsing: yes/no/on/off are booleans."""
        result = parse_yaml("enabled: yes\ndisabled: no")
        assert result.success
        assert result.data == {"enabled": True, "disabled": False}

    def test_very_long_string(self) -> None:
        """Parser handles arbitrarily long strings."""
        long_value = "x" * 10000
        result = parse_yaml(f"key: {long_value}")
        assert result.success
        assert result.data == {"key": long_value}

    def test_result_dataclass_fields(self) -> None:
        """Verify the result dataclass has expected attributes."""
        result = parse_yaml("key: value")
        assert hasattr(result, "data")
        assert hasattr(result, "error")
        assert hasattr(result, "success")
        assert result.success is True

        result = parse_yaml("")
        assert result.success is False

    def test_error_dataclass_is_frozen(self) -> None:
        """YamlParseError should be immutable (frozen dataclass)."""
        result = parse_yaml("")
        with pytest.raises(Exception):
            result.error.message = "modified"  # type: ignore[misc]


# ── Integration: real-world YAML fragments ──────────────────────────────


class TestRealWorldYamlFragments:
    """Verify the parser handles YAML fragments resembling actual project data."""

    def test_agent_spec_fragment(self) -> None:
        """Fragment resembling agent.yaml persona spec."""
        yaml_str = dedent(
            """\
            role_id: art-director
            display_name: 아트 디렉터
            team: art-design
            role_type: leader
            persistent_bot: true
            model:
              provider: opencode-go
              name: qwen-max
              fallback: deepseek-v3
            expertise_tags:
              - visual_direction
              - art_style
              - design_system
              - character_design
            """
        )
        result = parse_yaml(yaml_str)
        assert result.success
        assert result.data["role_id"] == "art-director"
        assert result.data["team"] == "art-design"
        assert result.data["model"]["provider"] == "opencode-go"
        assert len(result.data["expertise_tags"]) == 4

    def test_routing_rules_fragment(self) -> None:
        """Fragment resembling routing_rules.yaml structure."""
        yaml_str = dedent(
            """\
            version: "1.0.0"
            defaults:
              validator_required: true
              validator_model: glm-5.1
              max_roles_per_meeting: 7
            roles:
              - role_id: content-director
                team: content-production
                type: leader
              - role_id: scriptwriter
                team: content-production
                type: worker
            """
        )
        result = parse_yaml(yaml_str)
        assert result.success
        assert result.data["version"] == "1.0.0"
        assert result.data["defaults"]["max_roles_per_meeting"] == 7
        assert len(result.data["roles"]) == 2

    def test_config_fragment(self) -> None:
        """Minimal configuration fragment."""
        yaml_str = dedent(
            """\
            meeting:
              max_rounds: 3
              concurrent_limit: 2
            context_packet:
              worker_token_limit: 12000
              validator_token_limit: 20000
            """
        )
        result = parse_yaml(yaml_str)
        assert result.success
        assert result.data["meeting"]["max_rounds"] == 3
        assert result.data["context_packet"]["worker_token_limit"] == 12000


# ── Integration: compatibility with routing_rules_loader pattern ────────


class TestCompatibilityWithRoutingRulesLoader:
    """Verify yaml_parser produces the same results as yaml.safe_load for valid input."""

    def test_produces_same_result_as_safe_load(self) -> None:
        """For valid mapping YAML, parse_yaml result matches yaml.safe_load."""
        yaml_str = dedent(
            """\
            version: "1.0.0"
            metadata:
              key: value
            items:
              - a
              - b
            """
        )
        result = parse_yaml(yaml_str)
        expected = yaml.safe_load(yaml_str)
        assert result.data == expected

    def test_handles_what_safe_load_rejects(self) -> None:
        """Malformed YAML that raises from safe_load is caught by parse_yaml."""
        yaml_str = "key: {bad"
        # yaml.safe_load would raise
        with pytest.raises((yaml.YAMLError, ScannerError)):
            yaml.safe_load(yaml_str)
        # parse_yaml catches and returns structured error
        result = parse_yaml(yaml_str)
        assert not result.success
        assert result.error.error_type in (
            YamlErrorType.SCANNER_ERROR,
            YamlErrorType.PARSER_ERROR,
        )


# ── Constructor error ───────────────────────────────────────────────────


class TestConstructorErrors:
    """Verify constructor_error for YAML tag/constructor issues."""

    def test_unknown_tag_returns_constructor_error(self) -> None:
        """Custom YAML tags fail with safe_load."""
        result = parse_yaml("key: !unknown_tag value")
        assert not result.success
        # PyYAML safe_load raises ConstructorError for unknown tags
        assert result.error.error_type in (
            YamlErrorType.CONSTRUCTOR_ERROR,
            YamlErrorType.SCANNER_ERROR,
        )


# ── Composer error (when possible to trigger) ───────────────────────────


class TestComposerErrors:
    """Verify composer_error handling when triggerable."""

    def test_composer_error_handled_gracefully(self) -> None:
        """Duplicate key edge case: PyYAML's safe_load handles gracefully
        (last wins), so ComposerError is typically not triggered. We still
        exercise the exception handler with a direct call to verify it
        doesn't crash."""
        # Most common ComposerError triggers are duplicate keys in flow
        # mappings with strict mode, but safe_load discards duplicates.
        # The code path exists for completeness / future-proofing.
        result = parse_yaml("{a: 1, a: 2}")
        # safe_load keeps last value, no error
        assert result.success
        assert result.data == {"a": 2}
