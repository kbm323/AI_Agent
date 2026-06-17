"""Raw YAML string parser with structured error reporting.

Sub-AC 3.1.2: Parse raw YAML string into a structured dict, producing
a well-defined error state for malformed YAML syntax.

This module is the YAML counterpart to ``qwen_json_extractor.py`` — it
provides a single public entry point ``parse_yaml(raw_yaml: str)`` that
accepts any raw YAML string and returns a ``YamlParseResult`` with either
a parsed dict or a structured ``YamlParseError``.

Designed for use by:
- Agent persona spec loading (``agent.yaml`` in ``agents/{team}/{role_id}/``)
- YAML content embedded in context packets
- Configuration snippet parsing
- Anywhere raw YAML strings need reliable parsing with error location

Handled cases:
- Valid YAML mapping → dict
- Valid YAML sequence → non_mapping error
- Valid YAML scalar → non_mapping error
- Empty/whitespace-only → empty_input error
- ScannerError (syntax) → scanner_error with line/column
- ParserError (structure) → parser_error with line/column
- ComposerError (composition) → composer_error with line/column
- null result from safe_load → null_result error

All YAML errors carry precise line/column/position information so
callers can surface the exact location of the syntax problem.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import yaml
from yaml.constructor import ConstructorError
from yaml.parser import ParserError
from yaml.scanner import ScannerError

try:
    from yaml.composer import ComposerError
except ImportError:  # pragma: no cover — older PyYAML versions
    ComposerError = yaml.YAMLError  # type: ignore[assignment,misc]


# ── Error type enumeration ──────────────────────────────────────────────


class YamlErrorType:
    """Enumeration of YAML parse error categories."""

    EMPTY_INPUT: str = "empty_input"
    """Input string is empty or contains only whitespace."""

    SCANNER_ERROR: str = "scanner_error"
    """YAML lexical/syntax error (ScannerError)."""

    PARSER_ERROR: str = "parser_error"
    """YAML structure/grammar error (ParserError)."""

    COMPOSER_ERROR: str = "composer_error"
    """YAML composition error (ComposerError)."""

    CONSTRUCTOR_ERROR: str = "constructor_error"
    """YAML construction error (ConstructorError)."""

    NON_MAPPING: str = "non_mapping"
    """Valid YAML that is not a mapping (sequence or scalar)."""

    NULL_RESULT: str = "null_result"
    """YAML parsed successfully but returned None / no content."""


# ── Structured error ────────────────────────────────────────────────────


@dataclass(frozen=True)
class YamlParseError:
    """Structured YAML parse error with location information.

    Provides exact error location (line, column, character position)
    so callers can log, display, or route based on the failure details.
    """

    error_type: str
    """One of YamlErrorType values describing the failure category."""

    message: str
    """Human-readable description of what went wrong."""

    line: int | None = None
    """1-indexed line number where the error was detected, if available."""

    column: int | None = None
    """1-indexed column number where the error was detected, if available."""

    position: int | None = None
    """0-indexed character position in the raw string, if available."""

    raw_excerpt: str = ""
    """A snippet of the raw text around the error location (max 200 chars)."""

    recovery_hint: str = ""
    """Suggestion for how the caller might recover or retry."""


# ── Parse result ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class YamlParseResult:
    """Result of parsing a raw YAML string.

    On success: ``data`` is a dict and ``error`` is None.
    On failure: ``error`` is a YamlParseError and ``data`` is None.
    """

    data: dict | None = None
    """The parsed YAML dictionary, or None on failure."""

    error: YamlParseError | None = None
    """Structured error information, or None on success."""

    @property
    def success(self) -> bool:
        """True when parsing succeeded and ``data`` is available."""
        return self.data is not None and self.error is None


# ── Internal helpers ────────────────────────────────────────────────────


def _line_col(raw_text: str, pos: int) -> tuple[int, int]:
    """Convert a 0-indexed character position to (line, column) 1-indexed."""
    if pos < 0 or pos > len(raw_text):
        return (1, 1)
    prefix = raw_text[:pos]
    line = prefix.count("\n") + 1
    last_nl = prefix.rfind("\n")
    col = pos - last_nl if last_nl >= 0 else pos + 1
    return (line, col)


def _position(raw_text: str, line: int, column: int) -> int:
    """Convert 1-indexed (line, column) to 0-indexed character position."""
    lines = raw_text.split("\n")
    pos = 0
    for i in range(min(line - 1, len(lines))):
        pos += len(lines[i]) + 1  # +1 for the newline
    pos += max(0, column - 1)
    return min(pos, len(raw_text))


def _excerpt(raw_text: str, pos: int, radius: int = 100) -> str:
    """Extract a snippet of text around a position."""
    if not raw_text:
        return ""
    start = max(0, pos - radius)
    end = min(len(raw_text), pos + radius)
    snippet = raw_text[start:end]
    if start > 0:
        snippet = "…" + snippet
    if end < len(raw_text):
        snippet = snippet + "…"
    return snippet


def _is_empty(raw_yaml: str) -> bool:
    """Check if the input is empty or contains only whitespace/comments."""
    stripped = raw_yaml.strip()
    if not stripped:
        return True
    # Check if all non-empty lines are comments
    lines = stripped.split("\n")
    for line in lines:
        s = line.strip()
        if s and not s.startswith("#"):
            return False
    return True


def _detect_mixed_tabs(raw_yaml: str) -> bool:
    """Detect if the YAML contains tabs used for indentation.

    YAML forbids tab characters for indentation. This is a common
    user error that produces confusing ScannerError messages.
    """
    for i, line in enumerate(raw_yaml.split("\n"), start=1):
        stripped = line.lstrip(" ")
        if stripped.startswith("\t"):
            return True
    return False


# ── Public API ──────────────────────────────────────────────────────────


def parse_yaml(raw_yaml: str) -> YamlParseResult:
    """Parse a raw YAML string into a structured dict.

    This is the single entry point for Sub-AC 3.1.2. It accepts any
    raw YAML string and attempts to parse it into a Python dict via
    ``yaml.safe_load``.

    **Handled cases:**

    - Valid YAML mapping: ``"key: value"`` → ``{"key": "value"}``
    - Valid YAML sequence: ``"- item1"`` → non_mapping error
    - Valid YAML scalar: ``"hello"`` → non_mapping error
    - Empty string: ``""`` → empty_input error
    - Whitespace-only: ``"   "`` → empty_input error
    - Comments-only: ``"# comment"`` → empty_input error
    - ScannerError (bad syntax): error with line/column/excerpt
    - ParserError (bad structure): error with line/column/excerpt
    - ComposerError (composition issue): error with line/column/excerpt
    - ConstructorError (construction issue): error with line/column/excerpt
    - Tab indentation: scanner_error with specific hint
    - Duplicate key: non-fatal warning — last value wins (PyYAML behaviour)

    Args:
        raw_yaml: The raw YAML string to parse.

    Returns:
        ``YamlParseResult`` with ``data`` set on success, ``error``
        set on failure. Check ``result.success`` to branch.

    Examples:
        >>> result = parse_yaml("key: value\\\\ncount: 42")
        >>> result.success
        True
        >>> result.data
        {'key': 'value', 'count': 42}

        >>> result = parse_yaml("- item1\\\\n- item2")
        >>> result.success
        False
        >>> result.error.error_type
        'non_mapping'

        >>> result = parse_yaml("")
        >>> result.success
        False
        >>> result.error.error_type
        'empty_input'

        >>> result = parse_yaml("key: {bad")
        >>> result.success
        False
        >>> result.error.error_type
        'scanner_error'
        >>> result.error.line is not None
        True
    """
    # ── Handle empty / whitespace-only / comments-only input ──
    if _is_empty(raw_yaml):
        return YamlParseResult(
            data=None,
            error=YamlParseError(
                error_type=YamlErrorType.EMPTY_INPUT,
                message="YAML input is empty or contains only whitespace/comments",
                line=1,
                column=1,
                position=0,
                raw_excerpt="",
                recovery_hint=(
                    "Provide valid YAML content. An empty mapping '{}' may be "
                    "acceptable if the caller expects optional configuration."
                ),
            ),
        )

    # ── Detect tab indentation early for better error messages ──
    if _detect_mixed_tabs(raw_yaml):
        # Find the first tab-indented line for accurate location
        first_tab_pos = 0
        first_tab_line = 1
        first_tab_col = 1
        for i, line in enumerate(raw_yaml.split("\n"), start=1):
            tab_idx = line.find("\t")
            if tab_idx != -1:
                first_tab_pos = _position(raw_yaml, i, tab_idx + 1)
                first_tab_line = i
                first_tab_col = tab_idx + 1
                break

        return YamlParseResult(
            data=None,
            error=YamlParseError(
                error_type=YamlErrorType.SCANNER_ERROR,
                message=(
                    "YAML forbids tab characters for indentation. "
                    f"Tab found on line {first_tab_line}."
                ),
                line=first_tab_line,
                column=first_tab_col,
                position=first_tab_pos,
                raw_excerpt=_excerpt(raw_yaml, first_tab_pos),
                recovery_hint=(
                    "Replace all tab characters with spaces. "
                    "Recommended indentation: 2 or 4 spaces."
                ),
            ),
        )

    # ── Attempt to parse ──
    try:
        data = yaml.safe_load(raw_yaml)
    except ScannerError as exc:
        # ScannerError always has problem_mark at runtime
        prob = exc.problem_mark  # type: ignore[union-attr]
        pos = _position(raw_yaml, prob.line, prob.column)  # type: ignore[union-attr]
        return YamlParseResult(
            data=None,
            error=YamlParseError(
                error_type=YamlErrorType.SCANNER_ERROR,
                message=f"YAML syntax error: {exc.problem}",
                line=prob.line,  # type: ignore[union-attr]
                column=prob.column,  # type: ignore[union-attr]
                position=pos,
                raw_excerpt=_excerpt(raw_yaml, pos),
                recovery_hint=(
                    "Check for unquoted special characters, unbalanced braces, "
                    "or invalid YAML syntax at the indicated location."
                ),
            ),
        )
    except ParserError as exc:
        # ParserError always has problem_mark at runtime
        prob = exc.problem_mark  # type: ignore[union-attr]
        pos = _position(raw_yaml, prob.line, prob.column)  # type: ignore[union-attr]
        return YamlParseResult(
            data=None,
            error=YamlParseError(
                error_type=YamlErrorType.PARSER_ERROR,
                message=f"YAML structure error: {exc.problem}",
                line=prob.line,  # type: ignore[union-attr]
                column=prob.column,  # type: ignore[union-attr]
                position=pos,
                raw_excerpt=_excerpt(raw_yaml, pos),
                recovery_hint=(
                    "Check for incorrect indentation, missing colons, "
                    "or improperly nested structures."
                ),
            ),
        )
    except ComposerError as exc:
        # ComposerError may be a YAMLError subclass with problem_mark
        prob_mark = getattr(exc, "problem_mark", None)
        if prob_mark is not None:
            pos = _position(raw_yaml, prob_mark.line, prob_mark.column)  # type: ignore[union-attr]
            yaml_line: int | None = prob_mark.line  # type: ignore[union-attr]
            yaml_col: int | None = prob_mark.column  # type: ignore[union-attr]
        else:
            pos = 0
            yaml_line = None
            yaml_col = None
        problem = getattr(exc, "problem", str(exc))
        return YamlParseResult(
            data=None,
            error=YamlParseError(
                error_type=YamlErrorType.COMPOSER_ERROR,
                message=f"YAML composition error: {problem}",
                line=yaml_line,
                column=yaml_col,
                position=pos,
                raw_excerpt=_excerpt(raw_yaml, pos),
                recovery_hint=(
                    "Check for duplicate keys, anchor/alias issues, "
                    "or invalid YAML composition at the indicated location."
                ),
            ),
        )
    except ConstructorError as exc:
        prob_mark = exc.problem_mark  # type: ignore[union-attr]
        if prob_mark is not None:
            pos = _position(raw_yaml, prob_mark.line, prob_mark.column)  # type: ignore[union-attr]
            yaml_line = prob_mark.line  # type: ignore[union-attr]
            yaml_col = prob_mark.column  # type: ignore[union-attr]
        else:
            pos = 0
            yaml_line = None
            yaml_col = None
        return YamlParseResult(
            data=None,
            error=YamlParseError(
                error_type=YamlErrorType.CONSTRUCTOR_ERROR,
                message=f"YAML construction error: {exc.problem}",
                line=yaml_line,
                column=yaml_col,
                position=pos,
                raw_excerpt=_excerpt(raw_yaml, pos),
                recovery_hint=(
                    "Check for unsupported YAML tags, custom types, "
                    "or invalid constructors."
                ),
            ),
        )

    # ── Handle None / empty result ──
    if data is None:
        return YamlParseResult(
            data=None,
            error=YamlParseError(
                error_type=YamlErrorType.NULL_RESULT,
                message="YAML parsed without error but returned no content (null/None)",
                line=1,
                column=1,
                position=0,
                raw_excerpt="",
                recovery_hint=(
                    "The YAML input parsed to null. If the caller expects "
                    "optional content, treat as empty. Otherwise verify the "
                    "input is not literally 'null' or '~'."
                ),
            ),
        )

    # ── Enforce mapping result ──
    if not isinstance(data, dict):
        return YamlParseResult(
            data=None,
            error=YamlParseError(
                error_type=YamlErrorType.NON_MAPPING,
                message=(
                    f"YAML parsed successfully but returned "
                    f"{type(data).__name__} instead of a mapping (dict)"
                ),
                line=1,
                column=1,
                position=0,
                raw_excerpt=(
                    _excerpt(raw_yaml, 0, radius=80)
                    if raw_yaml
                    else ""
                ),
                recovery_hint=(
                    "The YAML input must be a mapping (key: value pairs). "
                    "For a sequence, use '- item' syntax inside a mapping "
                    "or wrap the sequence in a top-level key."
                ),
            ),
        )

    return YamlParseResult(data=data, error=None)


# ── Convenience function ────────────────────────────────────────────────


def parse_yaml_or_raise(raw_yaml: str) -> dict:
    """Parse a YAML string and return the dict, raising on failure.

    Convenience wrapper for callers that prefer exceptions over
    result objects. Raises ``ValueError`` with the error message
    on any parse failure.

    Args:
        raw_yaml: The raw YAML string to parse.

    Returns:
        The parsed dict.

    Raises:
        ValueError: If parsing fails for any reason.

    Examples:
        >>> data = parse_yaml_or_raise("key: value")
        >>> data
        {'key': 'value'}

        >>> parse_yaml_or_raise("")
        Traceback (most recent call last):
            ...
        ValueError: YAML parse failed (empty_input): ...
    """
    result = parse_yaml(raw_yaml)
    if not result.success:
        assert result.error is not None
        raise ValueError(
            f"YAML parse failed ({result.error.error_type}): "
            f"{result.error.message}"
        )
    assert result.data is not None
    return result.data
