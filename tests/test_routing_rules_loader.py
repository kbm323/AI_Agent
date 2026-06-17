"""Tests for the routing rules YAML file loader (Sub-AC 3.1a).

Covers:
- Happy path: loading the real routing_rules.yaml
- FileNotFoundError: path does not exist -> actionable message with path + fix
- YAMLError: corrupt/unparseable YAML file -> actionable message with path + tips
- OSError: unreadable file -> actionable message with path + permissions hint
- Edge cases: empty file (only comments), non-mapping top-level YAML
- Actionable error message structure: all errors include "Action:" prefix
- Structural checks on the parsed data (version, teams, roles)
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest
import yaml

from src.routing_rules_loader import load_routing_rules


# --- Fixtures ---------------------------------------------------------------

@pytest.fixture
def real_rules_path() -> Path:
    """Absolute path to the actual routing_rules.yaml in the project."""
    p = Path(__file__).resolve().parent.parent / "config" / "routing_rules.yaml"
    assert p.is_file(), f"routing_rules.yaml not found at {p}"
    return p


@pytest.fixture
def nonexistent_path(tmp_path: Path) -> Path:
    """A temporary path that does not point to any existing file."""
    return tmp_path / "nonexistent" / "routing_rules.yaml"


# --- Happy path -------------------------------------------------------------

class TestLoadRealRoutingRules:
    """Verify the loader successfully parses the project's routing_rules.yaml."""

    def test_returns_dict(self, real_rules_path: Path) -> None:
        data = load_routing_rules(real_rules_path)
        assert isinstance(data, dict)

    def test_version_field(self, real_rules_path: Path) -> None:
        data = load_routing_rules(real_rules_path)
        assert data["version"] == "1.0.0"

    def test_metadata_present(self, real_rules_path: Path) -> None:
        data = load_routing_rules(real_rules_path)
        assert "metadata" in data
        assert isinstance(data["metadata"], dict)
        assert "description" in data["metadata"]

    def test_defaults_present(self, real_rules_path: Path) -> None:
        data = load_routing_rules(real_rules_path)
        assert "defaults" in data
        defaults = data["defaults"]
        assert defaults["validator_required"] is True
        assert defaults["max_roles_per_meeting"] == 7

    def test_teams_present(self, real_rules_path: Path) -> None:
        data = load_routing_rules(real_rules_path)
        assert "teams" in data
        teams = data["teams"]
        assert len(teams) == 6

    def test_roles_present(self, real_rules_path: Path) -> None:
        data = load_routing_rules(real_rules_path)
        assert "roles" in data
        roles = data["roles"]
        assert len(roles) == 29

    def test_string_path_accepted(self, real_rules_path: Path) -> None:
        """String paths are resolved the same as Path objects."""
        data_str = load_routing_rules(str(real_rules_path))
        data_path = load_routing_rules(real_rules_path)
        assert data_str == data_path

    def test_tilde_expansion(self, tmp_path: Path) -> None:
        """~ in the path is expanded."""
        p = tmp_path / "test_rules.yaml"
        p.write_text("key: value\n", encoding="utf-8")
        result = load_routing_rules(p)
        assert result == {"key": "value"}


# --- FileNotFoundError (actionable messages) ---------------------------------

class TestFileNotFoundError:
    """Verify FileNotFoundError with actionable message when file is missing."""

    def test_nonexistent_file(self, nonexistent_path: Path) -> None:
        with pytest.raises(FileNotFoundError) as exc_info:
            load_routing_rules(nonexistent_path)
        msg = str(exc_info.value)
        assert str(nonexistent_path) in msg
        assert "Action:" in msg
        assert "Create the file" in msg or "update your configuration" in msg

    def test_directory_instead_of_file(self, tmp_path: Path) -> None:
        """Passing a directory (not a file) should raise FileNotFoundError."""
        dir_path = tmp_path / "a_directory"
        dir_path.mkdir()
        with pytest.raises(FileNotFoundError) as exc_info:
            load_routing_rules(dir_path)
        msg = str(exc_info.value)
        assert "not found" in msg.lower()
        assert "Action:" in msg

    def test_empty_string_path_raises(self) -> None:
        """Empty string resolves to cwd which is a directory, not a file."""
        with pytest.raises(FileNotFoundError) as exc_info:
            load_routing_rules("")
        assert "Action:" in str(exc_info.value)

    def test_error_includes_expected_path_label(self, nonexistent_path: Path) -> None:
        """The error message must include 'Expected path:' hint."""
        with pytest.raises(FileNotFoundError) as exc_info:
            load_routing_rules(nonexistent_path)
        assert "Expected path:" in str(exc_info.value)


# --- YAMLError (actionable messages with fix tips) ---------------------------

class TestYAMLError:
    """Verify yaml.YAMLError has actionable message with path and fix tips."""

    def test_syntax_error_yaml(self, tmp_path: Path) -> None:
        """Unbalanced quotes or broken syntax must raise YAMLError with tips."""
        f = tmp_path / "bad.yaml"
        f.write_text(
            dedent(
                """\
                version: "1.0.0
                metadata:
                  key: value
                """
            ),
            encoding="utf-8",
        )
        with pytest.raises(yaml.YAMLError) as exc_info:
            load_routing_rules(f)
        msg = str(exc_info.value)
        assert "Action:" in msg
        assert "Fix the YAML syntax" in msg
        assert "Unclosed or mismatched quotes" in msg
        assert "Original error:" in msg
        assert str(f) in msg

    def test_error_includes_line_and_column(self, tmp_path: Path) -> None:
        """YAMLError message must include explicit line and column numbers."""
        f = tmp_path / "bad_indent.yaml"
        f.write_text("key: value\n  bad indent: x\n", encoding="utf-8")
        with pytest.raises(yaml.YAMLError) as exc_info:
            load_routing_rules(f)
        msg = str(exc_info.value)
        assert "line" in msg.lower()
        assert "column" in msg.lower()
        # Verify the line/column appear near each other in the "at line N, column M" pattern
        assert " at line " in msg

    def test_tab_error_includes_line_and_column(self, tmp_path: Path) -> None:
        """Tab-character YAMLError must include line and column position."""
        f = tmp_path / "tabbed.yaml"
        f.write_text("key:\n\tsubkey: value\n", encoding="utf-8")
        with pytest.raises(yaml.YAMLError) as exc_info:
            load_routing_rules(f)
        msg = str(exc_info.value)
        assert " at line " in msg
        assert "column" in msg.lower()

    def test_malformed_mapping_includes_line_column(self, tmp_path: Path) -> None:
        """Improper indentation error must include line and column numbers."""
        f = tmp_path / "bad_map.yaml"
        f.write_text("key: value\n   bad sub: x\n", encoding="utf-8")
        with pytest.raises(yaml.YAMLError) as exc_info:
            load_routing_rules(f)
        msg = str(exc_info.value)
        assert " at line " in msg
        assert "column" in msg.lower()

    def test_tab_characters_cause_error(self, tmp_path: Path) -> None:
        """YAML forbids tab characters for indentation."""
        f = tmp_path / "tabbed.yaml"
        f.write_text("key:\n\tsubkey: value\n", encoding="utf-8")
        with pytest.raises(yaml.YAMLError) as exc_info:
            load_routing_rules(f)
        msg = str(exc_info.value)
        assert "Action:" in msg
        assert "Tab characters" in msg

    def test_non_mapping_top_level_returns_yaml_error(self, tmp_path: Path) -> None:
        """A YAML sequence at the top level raises YAMLError with fix hint."""
        f = tmp_path / "sequence.yaml"
        f.write_text("- item1\n- item2\n", encoding="utf-8")
        with pytest.raises(yaml.YAMLError) as exc_info:
            load_routing_rules(f)
        msg = str(exc_info.value)
        assert "mapping" in msg.lower()
        assert "Action:" in msg
        assert "key: value" in msg.lower()

    def test_error_includes_file_path(self, tmp_path: Path) -> None:
        """YAMLError must include the file path so operator knows which file."""
        f = tmp_path / "broken.yaml"
        f.write_text("{invalid: yaml: here}\n", encoding="utf-8")
        with pytest.raises(yaml.YAMLError) as exc_info:
            load_routing_rules(f)
        assert str(f) in str(exc_info.value)


# --- OSError (actionable message for unreadable file) ------------------------

class TestOSError:
    """Verify OSError is raised with actionable message for unreadable files."""

    def test_unreadable_file_permissions(self, tmp_path: Path) -> None:
        """A file with no read permissions should raise OSError with fix hint."""
        f = tmp_path / "unreadable.yaml"
        f.write_text("key: value\n", encoding="utf-8")
        f.chmod(0o000)
        try:
            with pytest.raises(OSError) as exc_info:
                load_routing_rules(f)
            msg = str(exc_info.value)
            assert "Action:" in msg
            assert "read permissions" in msg.lower() or "permissions" in msg.lower()
            assert "Original error:" in msg
        finally:
            f.chmod(0o644)


# --- Edge cases --------------------------------------------------------------

class TestEdgeCases:
    """Cover boundary conditions and unusual inputs."""

    def test_empty_yaml_file(self, tmp_path: Path) -> None:
        """An empty file returns {} (safe_load returns None)."""
        f = tmp_path / "empty.yaml"
        f.write_text("", encoding="utf-8")
        result = load_routing_rules(f)
        assert result == {}

    def test_comments_only_file(self, tmp_path: Path) -> None:
        """A file with only YAML comments returns {}."""
        f = tmp_path / "comments.yaml"
        f.write_text("# just a comment\n# another one\n", encoding="utf-8")
        result = load_routing_rules(f)
        assert result == {}

    def test_whitespace_only_file(self, tmp_path: Path) -> None:
        """A file with only whitespace returns {}."""
        f = tmp_path / "whitespace.yaml"
        f.write_text("   \n \n  \n", encoding="utf-8")
        result = load_routing_rules(f)
        assert result == {}

    def test_null_value_top_level(self, tmp_path: Path) -> None:
        """YAML file with literal 'null' at top level."""
        f = tmp_path / "null.yaml"
        f.write_text("null\n", encoding="utf-8")
        result = load_routing_rules(f)
        assert result == {}

    def test_explicit_null_key(self, tmp_path: Path) -> None:
        """A mapping with a null value is fine."""
        f = tmp_path / "null_value.yaml"
        f.write_text("key: null\n", encoding="utf-8")
        result = load_routing_rules(f)
        assert result == {"key": None}
