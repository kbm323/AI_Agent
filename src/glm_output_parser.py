"""GLM-5.1 structured output parser for validation verdicts.

Sub-AC 7.1b: Takes raw CLI output string from the GLM-5.1 validator call,
extracts the pass/fail boolean and confidence score from the structured
validation format (JSON or key-value delimited), and handles malformed
output by returning a structured parse error.

This module sits between the raw CLI output (``GlmCallResult.stdout``)
and the downstream validation pipeline (Coordinator, Codex secondary
validator).  It does NOT perform validation logic itself — it only
parses the structured output into a machine-readable verdict.

**Supported formats:**

1. **JSON** (primary)::

       {"verdict": "pass", "overall_score": 0.92, ...}

   Handles markdown fences, leading/trailing text, truncated JSON,
   and other common LLM output artefacts via ``qwen_json_extractor``.

2. **Key-value delimited** (fallback)::

       verdict: pass
       overall_score: 0.85

   Lines are split on the first ``:`` delimiter; whitespace is
   trimmed.  Supports any line-ending style.

**Verdict mapping (pass/fail boolean):**

+-------------------+--------+
| GLM verdict       | passed |
+===================+========+
| ``pass``          | True   |
+-------------------+--------+
| ``conditional_``\\ | True   |
| ``pass``          |        |
+-------------------+--------+
| ``revision_``\\    | False  |
| ``required``      |        |
+-------------------+--------+
| ``escalate``      | False  |
+-------------------+--------+
| ``fail``          | False  |
+-------------------+--------+
| any other value   | False  |
+-------------------+--------+

**Confidence score:**
The ``overall_score`` field (0.0–1.0) from the GLM output.  When
missing or unparseable, defaults to ``0.0`` and sets
``confidence_defaulted=True``.

Usage::

    from src.glm_output_parser import (
        GlmParseResult,
        GlmParseError,
        GlmParseErrorType,
        parse_glm_output,
    )

    result = parse_glm_output(glm_call_result.stdout)
    if result.success:
        print(f"Passed: {result.passed}, Confidence: {result.confidence}")
    else:
        print(f"Parse failed: {result.error.message}")
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from src.qwen_json_extractor import (
    QwenExtractionResult,
    QwenParseError,
    extract_json,
)

# ═════════════════════════════════════════════════════════════════════════
# Error types
# ═════════════════════════════════════════════════════════════════════════


class GlmParseErrorType:
    """Enumeration of GLM parse error categories."""

    EMPTY_OUTPUT: str = "empty_output"
    """Raw output is empty or whitespace-only."""

    NO_STRUCTURED_DATA: str = "no_structured_data"
    """No JSON or delimited key-value content found in output."""

    MALFORMED_OUTPUT: str = "malformed_output"
    """Output contains structural elements but cannot be parsed."""

    MISSING_VERDICT: str = "missing_verdict"
    """Structured data parsed but ``verdict`` field is absent."""

    INVALID_VERDICT: str = "invalid_verdict"
    """``verdict`` field present but value is not a recognised string."""

    MISSING_SCORE: str = "missing_score"
    """Structured data parsed but ``overall_score`` is absent."""

    INVALID_SCORE: str = "invalid_score"
    """``overall_score`` present but not a valid float."""


# ═════════════════════════════════════════════════════════════════════════
# Result data types
# ═════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class GlmParseError:
    """Structured parse error for GLM-5.1 output.

    Carries enough context for the Coordinator to log, report, or
    trigger the Codex secondary validator when the primary GLM-5.1
    output cannot be parsed.
    """

    error_type: str
    """One of ``GlmParseErrorType`` values."""

    message: str
    """Human-readable description of the failure."""

    raw_excerpt: str = ""
    """Snippet of the raw output around the error (max 300 chars)."""

    recovery_hint: str = ""
    """Suggestion for how the caller might recover or escalate."""


@dataclass(frozen=True)
class GlmParseResult:
    """Result of parsing GLM-5.1 structured validation output.

    Attributes:
        passed: Whether the GLM validator considers the meeting output
                to pass.  Maps directly from the ``verdict`` field.
        confidence: Float score in [0.0, 1.0] from ``overall_score``.
        confidence_defaulted: ``True`` when confidence was missing/
                              unparseable and defaulted to 0.0.
        verdict_raw: The raw verdict string from GLM (e.g. ``"pass"``,
                     ``"conditional_pass"``, ``"fail"``).
        error: ``None`` on success, ``GlmParseError`` on failure.
        parsed_data: The full parsed dict (when JSON), or ``None``
                     when delimited format was used.
        format_detected: Which format was detected: ``"json"`` or
                         ``"delimited"``.
    """

    passed: bool = False
    """Whether the GLM verdict maps to 'pass'."""

    confidence: float = 0.0
    """Extracted overall_score or defaulted 0.0."""

    confidence_defaulted: bool = False
    """True when confidence was missing and defaulted."""

    verdict_raw: str = ""
    """Raw verdict string from the parsed output."""

    error: GlmParseError | None = None
    """Parse error if unsuccessful, None on success."""

    parsed_data: dict[str, Any] | None = None
    """Full parsed dict when JSON format was used."""

    format_detected: str = ""
    """Format detected: ``"json"`` or ``"delimited"``."""

    @property
    def success(self) -> bool:
        """``True`` when parsing succeeded (``error`` is None)."""
        return self.error is None


# ═════════════════════════════════════════════════════════════════════════
# Verdict mapping
# ═════════════════════════════════════════════════════════════════════════

#: Recognised GLM verdict strings that map to passed=True.
_PASS_VERDICTS: frozenset[str] = frozenset({"pass", "conditional_pass"})

#: Recognised GLM verdict strings that map to passed=False.
_FAIL_VERDICTS: frozenset[str] = frozenset({
    "fail",
    "revision_required",
    "escalate",
})

#: All recognised verdict strings (for validation).
_ALL_VERDICTS: frozenset[str] = _PASS_VERDICTS | _FAIL_VERDICTS


def _map_verdict_to_bool(verdict: str) -> bool:
    """Map a GLM verdict string to a boolean pass/fail value.

    Args:
        verdict: Normalised (lowercased, stripped) verdict string.

    Returns:
        ``True`` for pass/conditional_pass, ``False`` otherwise.
    """
    return verdict in _PASS_VERDICTS


def _is_valid_verdict(verdict: str) -> bool:
    """Check if a verdict string is a recognised GLM verdict value."""
    return verdict in _ALL_VERDICTS


# ═════════════════════════════════════════════════════════════════════════
# JSON-format parsing
# ═════════════════════════════════════════════════════════════════════════


def _parse_json_output(
    raw_text: str,
) -> GlmParseResult:
    """Parse GLM-5.1 output in JSON format.

    Uses ``qwen_json_extractor.extract_json`` for robust JSON
    extraction (handles markdown fences, truncation, repair).
    Then extracts ``verdict`` and ``overall_score`` from the
    parsed dict.

    Args:
        raw_text: The raw stdout from a GLM-5.1 validator call.

    Returns:
        ``GlmParseResult`` with extracted fields or error.
    """
    # Extract JSON from raw text
    extraction: QwenExtractionResult = extract_json(raw_text)

    if not extraction.success:
        # JSON extraction failed entirely — map to GlmParseError
        qwen_err: QwenParseError = extraction.error  # type: ignore[assignment]
        return GlmParseResult(
            passed=False,
            error=GlmParseError(
                error_type=_map_qwen_error_type(qwen_err.error_type),
                message=f"JSON extraction failed: {qwen_err.message}",
                raw_excerpt=qwen_err.raw_excerpt[:300],
                recovery_hint=qwen_err.recovery_hint,
            ),
        )

    data: dict[str, Any] = extraction.data  # type: ignore[assignment]
    was_repaired = extraction.was_repaired

    # ── Extract verdict ──────────────────────────────────────────────
    verdict_result = _extract_verdict_from_dict(data)
    if verdict_result.error:
        return GlmParseResult(error=verdict_result.error)

    # ── Extract confidence ───────────────────────────────────────────
    confidence, confidence_defaulted = _extract_confidence_from_dict(data)

    # ── Build success result ─────────────────────────────────────────
    passed = _map_verdict_to_bool(verdict_result.verdict_raw)

    return GlmParseResult(
        passed=passed,
        confidence=confidence,
        confidence_defaulted=confidence_defaulted,
        verdict_raw=verdict_result.verdict_raw,
        error=None,
        parsed_data=data,
        format_detected="json",
    )


def _map_qwen_error_type(qwen_error_type: str) -> str:
    """Map a QwenParseError type to a GlmParseErrorType."""
    mapping = {
        "empty_response": GlmParseErrorType.EMPTY_OUTPUT,
        "no_json_found": GlmParseErrorType.NO_STRUCTURED_DATA,
        "malformed_json": GlmParseErrorType.MALFORMED_OUTPUT,
        "truncated_json": GlmParseErrorType.MALFORMED_OUTPUT,
        "non_json_content": GlmParseErrorType.NO_STRUCTURED_DATA,
    }
    return mapping.get(qwen_error_type, GlmParseErrorType.MALFORMED_OUTPUT)


@dataclass(frozen=True)
class _VerdictExtraction:
    """Intermediate result of extracting the verdict field."""

    verdict_raw: str = ""
    error: GlmParseError | None = None


def _extract_verdict_from_dict(data: dict[str, Any]) -> _VerdictExtraction:
    """Extract and validate the ``verdict`` field from a parsed dict.

    Args:
        data: Parsed JSON dict from GLM-5.1.

    Returns:
        ``_VerdictExtraction`` with verdict_raw on success or error on failure.
    """
    if "verdict" not in data:
        return _VerdictExtraction(
            error=GlmParseError(
                error_type=GlmParseErrorType.MISSING_VERDICT,
                message=(
                    "Parsed JSON does not contain a 'verdict' field. "
                    f"Available keys: {sorted(data.keys())}"
                ),
                raw_excerpt=str(data)[:300],
                recovery_hint=(
                    "The GLM-5.1 response is missing the required 'verdict' "
                    "field.  Check the prompt format sent to GLM-5.1 or "
                    "escalate to Codex GPT-5.5 secondary validator."
                ),
            ),
        )

    raw_verdict = data["verdict"]

    if not isinstance(raw_verdict, str):
        return _VerdictExtraction(
            error=GlmParseError(
                error_type=GlmParseErrorType.INVALID_VERDICT,
                message=(
                    f"'verdict' must be a string, got {type(raw_verdict).__name__}: "
                    f"{repr(raw_verdict)[:100]}"
                ),
                raw_excerpt=repr(data.get("verdict"))[:300],
                recovery_hint=(
                    "GLM-5.1 returned a non-string verdict.  Escalate to "
                    "Codex GPT-5.5 secondary validator."
                ),
            ),
        )

    verdict = raw_verdict.strip().lower()

    if not verdict:
        return _VerdictExtraction(
            error=GlmParseError(
                error_type=GlmParseErrorType.INVALID_VERDICT,
                message="'verdict' field is an empty string.",
                raw_excerpt=str(data)[:300],
                recovery_hint=(
                    "GLM-5.1 returned an empty verdict.  Escalate to "
                    "Codex GPT-5.5 secondary validator."
                ),
            ),
        )

    if not _is_valid_verdict(verdict):
        # Unknown verdict — treat as fail but don't error.  The caller
        # can decide whether to escalate based on risk_tags.
        return _VerdictExtraction(verdict_raw=verdict)

    return _VerdictExtraction(verdict_raw=verdict)


def _extract_confidence_from_dict(
    data: dict[str, Any],
) -> tuple[float, bool]:
    """Extract ``overall_score`` from a parsed dict as a float [0.0, 1.0].

    Args:
        data: Parsed JSON dict from GLM-5.1.

    Returns:
        ``(confidence, defaulted)`` — *defaulted* is True when the
        score was missing/unparseable and defaulted to 0.0.
    """
    if "overall_score" not in data:
        return (0.0, True)

    raw_score = data["overall_score"]

    # Boolean is not a valid score
    if isinstance(raw_score, bool):
        return (0.0, True)

    if isinstance(raw_score, (int, float)):
        score = float(raw_score)
        # Clamp to [0.0, 1.0]
        if score < 0.0 or score > 1.0:
            return (0.0, True)
        return (score, False)

    # Try parsing string
    if isinstance(raw_score, str):
        stripped = raw_score.strip()
        if not stripped:
            return (0.0, True)
        try:
            score = float(stripped)
            if score < 0.0 or score > 1.0:
                return (0.0, True)
            return (score, False)
        except ValueError:
            return (0.0, True)

    return (0.0, True)


# ═════════════════════════════════════════════════════════════════════════
# Key-value delimited format parsing
# ═════════════════════════════════════════════════════════════════════════

#: Regex to match ``key: value`` lines.
_KV_LINE_RE = re.compile(
    r"^\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*:\s*(.+?)\s*$",
    re.MULTILINE,
)


def _parse_delimited_output(raw_text: str) -> GlmParseResult:
    """Parse GLM-5.1 output in key-value delimited format.

    Each line should be ``key: value``.  Empty lines and lines
    without a colon are skipped.  Keys are normalised to lowercase
    with underscores.

    Args:
        raw_text: The raw stdout from a GLM-5.1 validator call.

    Returns:
        ``GlmParseResult`` with extracted fields or error.
    """
    kv: dict[str, str] = {}

    for line in raw_text.splitlines():
        m = _KV_LINE_RE.match(line)
        if not m:
            continue
        key = m.group(1).strip().lower()
        value = m.group(2).strip()
        kv[key] = value

    if not kv:
        return GlmParseResult(
            passed=False,
            error=GlmParseError(
                error_type=GlmParseErrorType.NO_STRUCTURED_DATA,
                message=(
                    "No key-value pairs found in delimited output. "
                    f"Raw output preview: {raw_text[:200]}"
                ),
                raw_excerpt=raw_text[:300],
                recovery_hint=(
                    "The GLM-5.1 output contains no parseable key-value "
                    "pairs.  Escalate to Codex GPT-5.5 secondary validator."
                ),
            ),
            format_detected="delimited",
        )

    # ── Extract verdict ──────────────────────────────────────────────
    verdict_raw = kv.get("verdict", "")
    if not verdict_raw:
        return GlmParseResult(
            passed=False,
            error=GlmParseError(
                error_type=GlmParseErrorType.MISSING_VERDICT,
                message=(
                    "No 'verdict' key found in delimited output. "
                    f"Available keys: {sorted(kv.keys())}"
                ),
                raw_excerpt=raw_text[:300],
                recovery_hint=(
                    "GLM-5.1 delimiter output missing 'verdict' field.  "
                    "Escalate to Codex GPT-5.5 secondary validator."
                ),
            ),
            format_detected="delimited",
        )

    verdict = verdict_raw.strip().lower()

    # ── Extract confidence ───────────────────────────────────────────
    confidence_raw = kv.get("overall_score", kv.get("confidence", ""))
    confidence: float = 0.0
    confidence_defaulted: bool = True

    if confidence_raw:
        try:
            parsed = float(confidence_raw.strip())
            if 0.0 <= parsed <= 1.0:
                confidence = parsed
                confidence_defaulted = False
        except ValueError:
            pass

    # ── Build result ─────────────────────────────────────────────────
    passed = _map_verdict_to_bool(verdict) if _is_valid_verdict(verdict) else False

    return GlmParseResult(
        passed=passed,
        confidence=confidence,
        confidence_defaulted=confidence_defaulted,
        verdict_raw=verdict,
        error=None,
        parsed_data=None,
        format_detected="delimited",
    )


# ═════════════════════════════════════════════════════════════════════════
# Public API
# ═════════════════════════════════════════════════════════════════════════


def _detect_format(raw_text: str) -> str:
    """Detect whether raw output is JSON or key-value delimited.

    Heuristic: if the text contains ``{``, it's likely JSON.
    Otherwise, look for ``verdict:`` or ``overall_score:`` patterns.

    Args:
        raw_text: Stripped raw output string.

    Returns:
        ``"json"`` or ``"delimited"``.
    """
    stripped = raw_text.strip()

    # If it contains a JSON brace, treat as JSON
    if "{" in stripped:
        return "json"

    # If it contains key-value pairs, treat as delimited
    if _KV_LINE_RE.search(stripped):
        return "delimited"

    # Default: try JSON first (extract_json handles fences/missing braces)
    return "json"


def parse_glm_output(raw_text: str, *, format_hint: str | None = None) -> GlmParseResult:
    """Parse raw GLM-5.1 validator output into a structured result.

    This is the main entry point for **Sub-AC 7.1b**.  It accepts the
    raw stdout string from a GLM-5.1 validator call (typically via
    ``GlmCallResult.stdout``) and extracts the pass/fail verdict and
    confidence score.

    **Parsing pipeline:**

    1. Accept empty/whitespace → ``EMPTY_OUTPUT`` error.
    2. Detect format (JSON or delimited).
    3. Extract verdict and confidence via the detected format parser.
    4. On failure, return ``GlmParseResult`` with ``error`` populated.

    **Verdict mapping:**

    +-------------------+--------+
    | GLM verdict       | passed |
    +===================+========+
    | ``pass``          | True   |
    +-------------------+--------+
    | ``conditional_``\\ | True   |
    | ``pass``          |        |
    +-------------------+--------+
    | ``fail``, ``revi`` | False  |
    | ``sion_required``,\\ |        |
    | ``escalate``      |        |
    +-------------------+--------+

    **Confidence:** extracted from ``overall_score`` (float 0.0–1.0).
    Defaults to 0.0 with ``confidence_defaulted=True`` when missing.

    Args:
        raw_text: The raw stdout string from a GLM-5.1 CLI call.
        format_hint: Override format detection (``"json"`` or
                     ``"delimited"``).  Useful when the caller knows
                     the format and auto-detection may be wrong.

    Returns:
        ``GlmParseResult`` — check ``result.success`` before consuming
        ``result.passed`` and ``result.confidence``.

    Examples:
        >>> result = parse_glm_output(
        ...     '{"verdict": "pass", "overall_score": 0.92}'
        ... )
        >>> result.success
        True
        >>> result.passed
        True
        >>> result.confidence
        0.92

        >>> result = parse_glm_output(
        ...     'verdict: fail\\noverall_score: 0.42'
        ... )
        >>> result.passed
        False
        >>> result.format_detected
        'delimited'

        >>> result = parse_glm_output("")
        >>> result.success
        False
        >>> result.error.error_type
        'empty_output'
    """
    # ── Handle empty input ───────────────────────────────────────────
    if not raw_text or not raw_text.strip():
        return GlmParseResult(
            passed=False,
            error=GlmParseError(
                error_type=GlmParseErrorType.EMPTY_OUTPUT,
                message="GLM-5.1 validator returned empty output.",
                raw_excerpt="",
                recovery_hint=(
                    "The GLM-5.1 CLI call produced no output.  Check the "
                    "subprocess exit code, API connectivity, or escalate "
                    "to Codex GPT-5.5 secondary validator."
                ),
            ),
        )

    stripped = raw_text.strip()

    # ── Detect format ────────────────────────────────────────────────
    fmt = format_hint if format_hint else _detect_format(stripped)

    # ── Parse by format ──────────────────────────────────────────────
    if fmt == "delimited":
        return _parse_delimited_output(stripped)

    # Default: JSON (which also handles fallback gracefully)
    return _parse_json_output(stripped)
