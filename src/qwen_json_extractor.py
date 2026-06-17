"""Robust Qwen LLM JSON extraction and parsing.

This module extracts structured JSON from raw Qwen LLM output strings.
Qwen responses often include markdown fences, leading/trailing commentary,
or malformed JSON.  This module handles those cases gracefully and returns
either a parsed dict or a structured error with line/position information.

Sub-AC 2b-1: Qwen JSON extraction and parsing — accepts raw string output
from Qwen, handles malformed/missing/empty responses, strips markdown
fences, returns parsed dict with line/position info for error location or
structured parse error.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Optional


# ── Error types ─────────────────────────────────────────────────────────

class ParseErrorType:
    """Enumeration of parse error categories."""

    EMPTY_RESPONSE: str = "empty_response"
    NO_JSON_FOUND: str = "no_json_found"
    MALFORMED_JSON: str = "malformed_json"
    TRUNCATED_JSON: str = "truncated_json"
    NON_JSON_CONTENT: str = "non_json_content"


# ── Structured error ────────────────────────────────────────────────────

@dataclass(frozen=True)
class QwenParseError:
    """Structured parse error with location information.

    Provides exact error location (line, column, character position)
    so callers can log, display, or route based on the failure details.
    """

    error_type: str
    """One of ParseErrorType values describing the failure category."""

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


# ── Extraction result ───────────────────────────────────────────────────

@dataclass(frozen=True)
class QwenExtractionResult:
    """Result of extracting and parsing JSON from Qwen raw output.

    On success: ``data`` is a dict and ``error`` is None.
    On failure: ``error`` is a QwenParseError and ``data`` is None.
    When ``was_repaired`` is True, the JSON was malformed or truncated
    but successfully repaired (caller should log a warning).
    """

    data: dict | None = None
    """The parsed JSON dictionary, or None on failure."""

    error: QwenParseError | None = None
    """Structured error information, or None on success."""

    was_repaired: bool = False
    """True when the JSON required repair before parsing successfully."""

    exit_condition: str = ""
    """Optional exit condition propagated from upstream (e.g. rate_limit_paused)."""

    @property
    def success(self) -> bool:
        """True when extraction succeeded and ``data`` is available."""
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


def _strip_fences(text: str) -> str:
    """Strip markdown code fences from text.

    Handles:
    - ```json ... ```
    - ``` ... ```
    - ````json ... ````  (quadruple backtick)
    - ~~~json ... ~~~

    Returns the text with fences removed, or the original text if no
    fences are found.
    """
    # Try to find a code block by matching opening/closing fences.
    # Use non-greedy match with DOTALL to capture content between fences.

    # Pattern for ```json ... ``` or ``` ... ```
    fence3 = re.compile(
        r"```(?:json|JSON)?\s*\n?(.*?)\n?```",
        re.DOTALL,
    )
    m = fence3.search(text)
    if m:
        return m.group(1).strip()

    # Pattern for ````json ... ```` (quadruple backtick)
    fence4 = re.compile(
        r"````(?:json|JSON)?\s*\n?(.*?)\n?````",
        re.DOTALL,
    )
    m = fence4.search(text)
    if m:
        return m.group(1).strip()

    # Pattern for ~~~json ... ~~~
    fence_tilde = re.compile(
        r"~~~(?:json|JSON)?\s*\n?(.*?)\n?~~~",
        re.DOTALL,
    )
    m = fence_tilde.search(text)
    if m:
        return m.group(1).strip()

    return text


def _walk_json_bounds(
    text: str, start: int, open_ch: str, close_ch: str
) -> tuple[int, int] | None:
    """Walk from *start* tracking *open_ch*/*close_ch* depth.

    Returns (start, end) when depth returns to 0 (balanced), or None
    if unclosed.
    """
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if ch == "\\" and in_string:
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return (start, i)
    return None


def _find_json_bounds(text: str) -> tuple[int, int] | None:
    """Find the best JSON object or array bounds in text.

    Scans all ``{`` and ``[`` positions and picks the largest balanced
    span.  When no balanced span is found but opening delimiters exist,
    returns ``(first_open, len(text) - 1)`` so the caller can attempt
    repair on truncated JSON.

    Returns (start, end) character positions (end inclusive), or None
    when no JSON delimiters exist at all.
    """
    # Collect all opening positions
    open_spans: list[tuple[int, int]] = []  # (start, end) for balanced spans
    first_open: int | None = None

    for i, ch in enumerate(text):
        if ch == "{":
            if first_open is None:
                first_open = i
            bounds = _walk_json_bounds(text, i, "{", "}")
            if bounds is not None:
                open_spans.append(bounds)
        elif ch == "[":
            if first_open is None:
                first_open = i
            bounds = _walk_json_bounds(text, i, "[", "]")
            if bounds is not None:
                open_spans.append(bounds)

    if open_spans:
        # Pick the largest balanced span
        return max(open_spans, key=lambda s: s[1] - s[0])

    if first_open is not None:
        # Unclosed delimiters — return from first open to end for repair
        return (first_open, len(text) - 1)

    return None


def _attempt_json_repair(
    json_text: str, raw_text: str, json_start: int
) -> tuple[dict | None, QwenParseError | None, bool]:
    """Try to repair truncated or slightly malformed JSON.

    Returns:
        (data, error, was_repaired) — was_repaired is True when
        repair was needed and successful.
    """
    # Strategy 1: parse as-is
    try:
        return (json.loads(json_text), None, False)
    except json.JSONDecodeError as exc:
        original_error = exc

    # Strategy 2: close all unclosed delimiters + fix trailing commas
    repaired = json_text.rstrip()
    # Remove trailing comma (common LLM error)
    if repaired.endswith(","):
        repaired = repaired[:-1].rstrip()
    # Close all unclosed delimiters (braces, brackets, strings)
    repaired = _close_unclosed_delimiters(repaired)

    try:
        return (json.loads(repaired), None, True)
    except json.JSONDecodeError:
        pass

    # Strategy 3: try truncating to last valid token
    truncated = _truncate_to_last_valid_token(json_text)
    if truncated:
        try:
            return (json.loads(truncated), None, True)
        except json.JSONDecodeError:
            pass

    # All repair strategies failed
    line, col = _line_col(raw_text, json_start + original_error.pos)
    return (
        None,
        QwenParseError(
            error_type=(
                ParseErrorType.TRUNCATED_JSON
                if _is_truncation(json_text)
                else ParseErrorType.MALFORMED_JSON
            ),
            message=f"JSON parse failed after repair attempts: {original_error.msg}",
            line=line,
            column=col,
            position=json_start + original_error.pos,
            raw_excerpt=_excerpt(raw_text, json_start + original_error.pos),
            recovery_hint=(
                "The Qwen response appears truncated. "
                "Consider increasing max_tokens or retrying the Qwen call."
                if _is_truncation(json_text)
                else "The JSON is malformed. Check for unescaped quotes, "
                "missing commas, or invalid syntax in the Qwen response."
            ),
        ),
        False,
    )


def _count_unclosed_braces(text: str) -> int:
    """Count unclosed braces/brackets (positive = unclosed opening)."""
    depth = 0
    in_string = False
    escape = False
    for ch in text:
        if escape:
            escape = False
            continue
        if ch == "\\" and in_string:
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
    return depth


def _close_unclosed_delimiters(text: str) -> str:
    """Append closing delimiters for unclosed braces/brackets/strings."""
    result = text

    # Track brace and bracket context ignoring strings
    brace_stack: list[str] = []
    in_string = False
    escape = False
    for ch in result:
        if escape:
            escape = False
            continue
        if ch == "\\" and in_string:
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            brace_stack.append("}")
        elif ch == "}":
            if brace_stack and brace_stack[-1] == "}":
                brace_stack.pop()
        elif ch == "[":
            brace_stack.append("]")
        elif ch == "]":
            if brace_stack and brace_stack[-1] == "]":
                brace_stack.pop()

    # Close unclosed strings
    if in_string:
        result += '"'

    # Close unclosed delimiters in reverse order
    while brace_stack:
        result += brace_stack.pop()

    return result


def _is_truncation(json_text: str) -> bool:
    """Heuristic: check if the text looks truncated."""
    stripped = json_text.rstrip()
    if not stripped:
        return True
    # If the last non-whitespace character is not a closing delimiter
    # or a valid JSON terminal, it's likely truncated
    last_char = stripped[-1]
    valid_terminals = {"}", "]", '"', "0", "1", "2", "3", "4", "5", "6", "7", "8", "9",
                       "e", "E", "l", "e", "n", "r", "u", "s", "a", "f", "L", "N", "A", "F"}
    if last_char not in valid_terminals:
        return True
    # Check if braces/brackets are unbalanced
    if _count_unclosed_braces(stripped) > 0:
        return True
    return False


def _truncate_to_last_valid_token(json_text: str) -> str | None:
    """Try to find the last valid JSON token and truncate to it.

    Walks backward from the end, finding the last position that could
    be a complete JSON value boundary.
    """
    # Simple heuristic: find the last closing brace or bracket that
    # doesn't leave unbalanced delimiters
    text = json_text.rstrip()

    # Try removing trailing commas
    while text and text[-1] in (",", "\n", "\r", "\t", " "):
        text = text[:-1].rstrip()

    if not text:
        return None

    # If it already ends with a closing brace/bracket, check balance
    if text[-1] in "}]":
        if _count_unclosed_braces(text) == 0:
            return text

    # Try chopping from end until we find balanced state
    # Start from the last comma and try adding closing delimiters
    last_comma = text.rfind(",")
    if last_comma > 0:
        candidate = text[:last_comma].rstrip()
        candidate = _close_unclosed_delimiters(candidate)
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            pass

    return None


# ── Public API ──────────────────────────────────────────────────────────

def extract_json(raw_text: str) -> QwenExtractionResult:
    """Extract and parse JSON from raw Qwen LLM output.

    This is the main entry point for Sub-AC 2b-1.  It accepts any raw
    string output from a Qwen LLM call and attempts to extract a valid
    JSON dictionary, handling common Qwen output artefacts.

    **Handled cases:**

    - Clean JSON: ``{"key": "value"}``
    - Markdown-fenced: ```` ```json\\n{...}\\n``` ````
    - Leading/trailing text: ``Preamble... {"key": "value"} ...postscript``
    - Truncated JSON: ``{"key": "val`` — repair attempt with close delimiters
    - Malformed JSON: stray characters, unescaped quotes — error with location
    - Empty string: ``""`` — empty_response error
    - Whitespace-only: ``"   "`` — empty_response error
    - Non-JSON content: ``"Just some text"`` — no_json_found error

    Args:
        raw_text: The raw string response from a Qwen LLM call.

    Returns:
        QwenExtractionResult with ``data`` set on success, ``error``
        set on failure.  Check ``result.success`` to branch.

    Examples:
        >>> result = extract_json('{"key": "value"}')
        >>> result.success
        True
        >>> result.data
        {'key': 'value'}

        >>> result = extract_json("```json\\n{\\"a\\": 1}\\n```")
        >>> result.data
        {'a': 1}

        >>> result = extract_json("")
        >>> result.success
        False
        >>> result.error.error_type
        'empty_response'
    """
    # ── Handle empty / whitespace-only input ──
    if not raw_text or not raw_text.strip():
        return QwenExtractionResult(
            data=None,
            error=QwenParseError(
                error_type=ParseErrorType.EMPTY_RESPONSE,
                message="Qwen returned an empty response",
                line=1,
                column=1,
                position=0,
                raw_excerpt="",
                recovery_hint=(
                    "The Qwen call returned no output. "
                    "Check API connectivity, rate limits, or model availability."
                ),
            ),
        )

    original = raw_text
    text = raw_text.strip()

    # ── Strip markdown fences ──
    text = _strip_fences(text)

    # ── Find JSON bounds ──
    bounds = _find_json_bounds(text)
    if bounds is None:
        # No JSON structure found at all
        return QwenExtractionResult(
            data=None,
            error=QwenParseError(
                error_type=ParseErrorType.NO_JSON_FOUND,
                message="No JSON object or array found in Qwen response",
                line=1,
                column=1,
                position=0,
                raw_excerpt=_excerpt(original, 0, radius=100),
                recovery_hint=(
                    "The Qwen response contains no JSON structure. "
                    "Check that the prompt instructs Qwen to output JSON."
                ),
            ),
        )

    json_start, json_end = bounds
    json_text = text[json_start : json_end + 1]

    # ── Parse with repair attempts ──
    data, error, was_repaired = _attempt_json_repair(
        json_text, original, json_start
    )

    if error is not None:
        return QwenExtractionResult(data=None, error=error)

    # ── Post-condition: must be a dict ──
    if not isinstance(data, dict):
        return QwenExtractionResult(
            data=None,
            error=QwenParseError(
                error_type=ParseErrorType.NO_JSON_FOUND,
                message=(
                    f"Extracted JSON is a {type(data).__name__}, "
                    f"expected a dict/object. Qwen may have returned "
                    f"an array instead of an object."
                ),
                line=_line_col(original, json_start)[0],
                column=1,
                position=json_start,
                raw_excerpt=_excerpt(original, json_start),
                recovery_hint="Ensure the Qwen prompt requests a JSON object (not array).",
            ),
        )

    return QwenExtractionResult(data=data, error=None, was_repaired=was_repaired)
