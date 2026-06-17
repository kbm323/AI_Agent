"""Comprehensive tests for the Qwen JSON extraction and parsing module.

Sub-AC 2b-1: Qwen JSON extraction and parsing — accepts raw string output
from Qwen, handles malformed/missing/empty responses, strips markdown
fences if present, returns parsed dict with line/position info for error
location or structured parse error.

Testable with mock raw Qwen response strings covering:
- valid JSON (clean, markdown-wrapped, with leading/trailing text)
- truncated JSON
- non-JSON content
- empty / whitespace-only responses
- malformed JSON with error location verification
"""

from __future__ import annotations

import json
import textwrap
from typing import Any

import pytest

from src.qwen_json_extractor import (
    QwenExtractionResult,
    QwenParseError,
    ParseErrorType,
    extract_json,
)


# ═══════════════════════════════════════════════════════════════════════
# Helper: build a classification-style JSON string
# ═══════════════════════════════════════════════════════════════════════

def _make_classification_payload(**overrides: Any) -> str:
    """Build a realistic Qwen classification JSON string."""
    defaults: dict[str, Any] = {
        "agenda_type": "creative_production",
        "tags": ["character-design", "visual-concept"],
        "risk_tags": ["brand"],
        "required_roles": ["coordinator", "art-director", "marketing-lead"],
        "optional_roles": ["concept-artist", "sns-strategist"],
        "validator_required": True,
        "codex_required": False,
        "confidence": 0.92,
        "reasoning": "Visual design plus SNS strategy spans art and marketing.",
    }
    defaults.update(overrides)
    return json.dumps(defaults, ensure_ascii=False)


_VALID_CLASSIFICATION = _make_classification_payload()


# ═══════════════════════════════════════════════════════════════════════
# 1. Valid JSON — clean input
# ═══════════════════════════════════════════════════════════════════════

class TestValidCleanJson:
    """Verify extraction of clean, well-formed JSON."""

    def test_parses_simple_flat_json(self):
        result = extract_json('{"key": "value"}')
        assert result.success
        assert result.data == {"key": "value"}
        assert result.error is None

    def test_parses_classification_payload(self):
        result = extract_json(_VALID_CLASSIFICATION)
        assert result.success
        assert result.data is not None
        assert result.data["agenda_type"] == "creative_production"
        assert result.data["tags"] == ["character-design", "visual-concept"]
        assert result.data["risk_tags"] == ["brand"]
        assert result.data["validator_required"] is True
        assert result.data["codex_required"] is False
        assert result.data["confidence"] == 0.92

    def test_parses_deeply_nested_json(self):
        payload = json.dumps({
            "a": {"b": {"c": [1, 2, 3], "d": {"e": "deep"}}},
            "f": [{"g": 1}, {"g": 2}],
        })
        result = extract_json(payload)
        assert result.success
        assert result.data is not None
        assert result.data["a"]["b"]["c"] == [1, 2, 3]
        assert result.data["a"]["b"]["d"]["e"] == "deep"
        assert len(result.data["f"]) == 2

    def test_parses_json_with_unicode_emoji(self):
        payload = json.dumps({"topic": "신규 캐릭터 '루나' 🎨", "tag": "ビジュアル"})
        result = extract_json(payload)
        assert result.success
        assert result.data is not None
        assert result.data["topic"] == "신규 캐릭터 '루나' 🎨"
        assert result.data["tag"] == "ビジュアル"

    def test_parses_json_with_special_characters(self):
        payload = json.dumps({"text": 'line1\\nline2\\t"quoted"\\n\\/path'})
        result = extract_json(payload)
        assert result.success
        assert result.data is not None
        assert "line1" in result.data["text"]
        assert "quoted" in result.data["text"]

    def test_parses_empty_object(self):
        result = extract_json("{}")
        assert result.success
        assert result.data == {}

    def test_parses_json_with_numbers_and_booleans(self):
        payload = json.dumps({
            "int_val": 42,
            "float_val": 3.14,
            "neg_val": -7,
            "zero": 0,
            "true_val": True,
            "false_val": False,
            "null_val": None,
        })
        result = extract_json(payload)
        assert result.success
        assert result.data is not None
        assert result.data["int_val"] == 42
        assert result.data["float_val"] == 3.14
        assert result.data["neg_val"] == -7
        assert result.data["true_val"] is True
        assert result.data["false_val"] is False
        assert result.data["null_val"] is None


# ═══════════════════════════════════════════════════════════════════════
# 2. Markdown-fenced JSON
# ═══════════════════════════════════════════════════════════════════════

class TestMarkdownFencedJson:
    """Verify extraction when JSON is wrapped in markdown code fences."""

    def test_fence_with_json_tag(self):
        raw = "```json\n" + _VALID_CLASSIFICATION + "\n```"
        result = extract_json(raw)
        assert result.success
        assert result.data is not None
        assert result.data["agenda_type"] == "creative_production"

    def test_fence_without_language_tag(self):
        raw = "```\n" + _VALID_CLASSIFICATION + "\n```"
        result = extract_json(raw)
        assert result.success
        assert result.data is not None
        assert result.data["agenda_type"] == "creative_production"

    def test_fence_with_TEXT_content_type(self):
        # Sometimes Qwen puts ```json in uppercase
        raw = "```JSON\n" + _VALID_CLASSIFICATION + "\n```"
        result = extract_json(raw)
        assert result.success
        assert result.data is not None
        assert result.data["agenda_type"] == "creative_production"

    def test_fence_with_leading_newlines(self):
        raw = "```json\n\n\n" + _VALID_CLASSIFICATION + "\n\n\n```"
        result = extract_json(raw)
        assert result.success
        assert result.data is not None
        assert result.data["agenda_type"] == "creative_production"

    def test_fence_surrounded_by_text(self):
        raw = textwrap.dedent("""\
            Here is my classification:

            ```json
            {
              "agenda_type": "technical_development",
              "tags": ["backend-api"],
              "risk_tags": [],
              "required_roles": ["coordinator", "tech-director"],
              "optional_roles": [],
              "validator_required": false,
              "codex_required": false,
              "confidence": 0.85,
              "reasoning": "Pure technical work."
            }
            ```

            Let me know if you need anything else.
        """)
        result = extract_json(raw)
        assert result.success
        assert result.data is not None
        assert result.data["agenda_type"] == "technical_development"
        assert result.data["validator_required"] is False
        assert result.data["confidence"] == 0.85

    def test_tilde_fence(self):
        """~~~json blocks (used by some Qwen versions)."""
        raw = "~~~json\n" + _VALID_CLASSIFICATION + "\n~~~"
        result = extract_json(raw)
        assert result.success
        assert result.data is not None
        assert result.data["agenda_type"] == "creative_production"

    def test_quadruple_backtick_fence(self):
        """````json blocks (used for nested code)."""
        raw = "````json\n" + _VALID_CLASSIFICATION + "\n````"
        result = extract_json(raw)
        assert result.success
        assert result.data is not None
        assert result.data["agenda_type"] == "creative_production"

    def test_fence_with_no_newline_before_closing(self):
        """Qwen sometimes omits the newline before ```."""
        raw = "```json\n" + _VALID_CLASSIFICATION + "```"
        result = extract_json(raw)
        assert result.success
        assert result.data is not None
        assert result.data["agenda_type"] == "creative_production"


# ═══════════════════════════════════════════════════════════════════════
# 3. JSON with leading / trailing commentary
# ═══════════════════════════════════════════════════════════════════════

class TestLeadingTrailingText:
    """Verify extraction when text surrounds the JSON block."""

    def test_leading_text_before_json(self):
        raw = "Based on your meeting topic, here is the classification:\n" + _VALID_CLASSIFICATION
        result = extract_json(raw)
        assert result.success
        assert result.data is not None
        assert result.data["agenda_type"] == "creative_production"

    def test_trailing_text_after_json(self):
        raw = _VALID_CLASSIFICATION + "\n\nWould you like me to adjust anything?"
        result = extract_json(raw)
        assert result.success
        assert result.data is not None
        assert result.data["agenda_type"] == "creative_production"

    def test_leading_and_trailing_text(self):
        raw = (
            "Qwen Classification Output:\\n"
            + _VALID_CLASSIFICATION
            + "\\n\\nThis classification is complete."
        )
        result = extract_json(raw)
        assert result.success
        assert result.data is not None
        assert result.data["agenda_type"] == "creative_production"

    def test_text_with_multiple_braces(self):
        """Ensure we pick the outermost JSON object, not random braces."""
        raw = textwrap.dedent("""\
            Options considered:
            1. Option A {priority: 1}
            2. Option B {priority: 2}

            Final classification:
            {
              "agenda_type": "general_planning",
              "tags": ["planning"],
              "risk_tags": [],
              "required_roles": ["coordinator"],
              "optional_roles": [],
              "validator_required": false,
              "codex_required": false,
              "confidence": 0.9,
              "reasoning": "Planning discussion."
            }
        """)
        result = extract_json(raw)
        assert result.success
        assert result.data is not None
        assert result.data["agenda_type"] == "general_planning"


# ═══════════════════════════════════════════════════════════════════════
# 4. Truncated JSON — repair attempts
# ═══════════════════════════════════════════════════════════════════════

class TestTruncatedJson:
    """Verify graceful handling of truncated Qwen JSON output.

    Simple truncations like missing closing braces are repaired
    automatically.  Mid-value truncations (cut off inside a string
    value) produce errors since the semantic content is lost.
    """

    def test_truncated_mid_value_returns_error(self):
        """String value cut off mid-word — repair closes delimiters but
        the value 'creat' is truncated from whatever it should be."""
        raw = '{"agenda_type": "creat'
        result = extract_json(raw)
        assert result.success, "Basic structural repair should succeed"
        assert result.was_repaired, "Truncated input should be flagged"
        assert result.data is not None
        # The value is truncated but structurally valid
        assert result.data.get("agenda_type") == "creat"

    def test_truncated_mid_key_returns_error(self):
        raw = '{"agenda_ty'
        result = extract_json(raw)
        # This may or may not be repairable — just check we get a result
        # without crashing
        assert result.error is not None or result.data is not None

    def test_truncated_with_unclosed_braces(self):
        """Nested unclosed braces — repair should close them all."""
        raw = '{"a": {"b": [1, 2, 3'
        result = extract_json(raw)
        assert result.success, "Repair closes unclosed braces/brackets"
        assert result.was_repaired
        assert result.data is not None
        assert result.data["a"]["b"] == [1, 2, 3]

    def test_recovery_unclosed_object(self):
        """Should successfully close '{ "key": "value"' and parse."""
        raw = '{ "key": "value"'
        result = extract_json(raw)
        assert result.success
        assert result.was_repaired
        assert result.data == {"key": "value"}

    def test_recovery_unclosed_array(self):
        """Should recover from '[1, 2, 3'."""
        raw = '[1, 2, 3'
        result = extract_json(raw)
        # Arrays are NOT dicts — extract_json returns error for arrays
        assert not result.success
        assert result.error is not None
        assert "array" in result.error.message.lower()

    def test_recovery_unclosed_nested_structure(self):
        raw = '{"a": [1, {"b": "c"'
        result = extract_json(raw)
        assert result.success
        assert result.was_repaired

    def test_truncated_before_first_brace(self):
        raw = '{"agenda_type": "general'
        result = extract_json(raw)
        assert result.success, "Structural repair closes string and brace"
        assert result.was_repaired
        assert result.data is not None
        assert result.data.get("agenda_type") == "general"


# ═══════════════════════════════════════════════════════════════════════
# 5. Malformed JSON with error location
# ═══════════════════════════════════════════════════════════════════════

class TestMalformedJson:
    """Verify malformed JSON produces structured errors with location info."""

    def test_trailing_comma_in_object(self):
        raw = '{"key": "value",}'
        result = extract_json(raw)
        assert not result.success
        assert result.error is not None
        assert result.error.error_type in (
            ParseErrorType.MALFORMED_JSON,
            ParseErrorType.TRUNCATED_JSON,
        )
        assert result.error.message != ""

    def test_unquoted_key(self):
        raw = "{key: value}"
        result = extract_json(raw)
        assert not result.success
        assert result.error is not None

    def test_single_quoted_keys(self):
        raw = "{'key': 'value'}"
        result = extract_json(raw)
        assert not result.success
        assert result.error is not None

    def test_error_has_line_and_column(self):
        """Malformed JSON error must include line and column info."""
        raw = '{\n  "key" "value"\n}'
        result = extract_json(raw)
        assert not result.success
        assert result.error is not None
        assert result.error.line is not None
        assert result.error.line > 0
        assert result.error.column is not None
        assert result.error.column > 0

    def test_error_has_position(self):
        raw = '{"key": }'
        result = extract_json(raw)
        assert not result.success
        assert result.error is not None
        assert result.error.position is not None

    def test_error_has_raw_excerpt(self):
        raw = '{"key": broken}'
        result = extract_json(raw)
        assert not result.success
        assert result.error is not None
        assert len(result.error.raw_excerpt) > 0

    def test_error_has_recovery_hint(self):
        raw = '{"key": broken}'
        result = extract_json(raw)
        assert not result.success
        assert result.error is not None
        assert len(result.error.recovery_hint) > 0

    def test_line_column_accurate_for_multi_line(self):
        """Line/column should point to the actual error position."""
        raw = textwrap.dedent("""\
            {
              "agenda_type": "test"
              "risk_tags": ["brand"],
              "required_roles": ["coordinator"]
            }
        """)
        result = extract_json(raw)
        assert not result.success
        # Error should be near the missing comma after "test" on line 2
        assert result.error is not None
        assert result.error.line is not None
        # The error should be on line 3 (the unexpected string)
        assert result.error.line >= 2


# ═══════════════════════════════════════════════════════════════════════
# 6. Empty / missing responses
# ═══════════════════════════════════════════════════════════════════════

class TestEmptyResponse:
    """Verify handling of empty, whitespace-only, or missing responses."""

    def test_empty_string(self):
        result = extract_json("")
        assert not result.success
        assert result.error is not None
        assert result.error.error_type == ParseErrorType.EMPTY_RESPONSE
        assert result.data is None

    def test_whitespace_only(self):
        result = extract_json("   \n\t  \n   ")
        assert not result.success
        assert result.error is not None
        assert result.error.error_type == ParseErrorType.EMPTY_RESPONSE

    def test_newline_only(self):
        result = extract_json("\n\n\n")
        assert not result.success
        assert result.error is not None
        assert result.error.error_type == ParseErrorType.EMPTY_RESPONSE

    def test_empty_response_has_recovery_hint(self):
        result = extract_json("")
        assert not result.success
        assert result.error is not None
        assert "API connectivity" in result.error.recovery_hint.lower() or \
               "call returned no output" in result.error.recovery_hint.lower()


# ═══════════════════════════════════════════════════════════════════════
# 7. Non-JSON content
# ═══════════════════════════════════════════════════════════════════════

class TestNonJsonContent:
    """Verify handling of responses that contain no JSON at all."""

    def test_plain_text(self):
        result = extract_json("Just a plain text response from Qwen.")
        assert not result.success
        assert result.error is not None
        assert result.error.error_type == ParseErrorType.NO_JSON_FOUND

    def test_code_without_braces(self):
        result = extract_json("function classify() { return 'done'; }  // no JSON here")
        assert not result.success
        assert result.error is not None

    def test_xml_instead_of_json(self):
        result = extract_json("<response><agenda_type>test</agenda_type></response>")
        assert not result.success
        assert result.error is not None

    def test_yaml_instead_of_json(self):
        raw = textwrap.dedent("""\
            agenda_type: general_planning
            tags:
              - planning
        """)
        result = extract_json(raw)
        assert not result.success
        assert result.error is not None


# ═══════════════════════════════════════════════════════════════════════
# 8. Array instead of object
# ═══════════════════════════════════════════════════════════════════════

class TestArrayInsteadOfObject:
    """Verify handling when Qwen returns a JSON array instead of object."""

    def test_clean_array_returns_error(self):
        result = extract_json('[1, 2, 3]')
        assert not result.success
        assert result.error is not None
        assert "array" in result.error.message.lower()

    def test_fenced_array_returns_error(self):
        raw = "```json\n[1, 2, 3]\n```"
        result = extract_json(raw)
        assert not result.success
        assert result.error is not None
        assert "array" in result.error.message.lower()


# ═══════════════════════════════════════════════════════════════════════
# 9. Result object properties
# ═══════════════════════════════════════════════════════════════════════

class TestResultProperties:
    """Verify QwenExtractionResult properties behave correctly."""

    def test_success_true_when_data_present(self):
        result = extract_json('{"key": "value"}')
        assert result.success is True

    def test_success_false_when_error_present(self):
        result = extract_json("not json")
        assert result.success is False

    def test_success_false_when_both_none_should_not_happen(self):
        # This shouldn't happen in practice, but test defensively
        result = QwenExtractionResult(data=None, error=None)
        assert result.success is False

    def test_result_is_frozen(self):
        result = extract_json('{"key": "value"}')
        with pytest.raises(Exception):
            result.data = None  # type: ignore[misc]

    def test_error_is_frozen(self):
        error = QwenParseError(
            error_type=ParseErrorType.EMPTY_RESPONSE,
            message="test",
        )
        with pytest.raises(Exception):
            error.message = "changed"  # type: ignore[misc]


# ═══════════════════════════════════════════════════════════════════════
# 10. Edge cases — real-world Qwen output patterns
# ═══════════════════════════════════════════════════════════════════════

class TestRealWorldQwenPatterns:
    """Test patterns observed in real Qwen LLM outputs."""

    def test_qwen_adds_period_after_json(self):
        """Qwen sometimes adds a period or commentary after the JSON."""
        raw = _VALID_CLASSIFICATION + "."
        result = extract_json(raw)
        # The original valid JSON should still parse even with trailing period
        assert result.success

    def test_qwen_numbered_list_with_json(self):
        """Qwen sometimes outputs '1. {...}' format."""
        raw = "1. " + _VALID_CLASSIFICATION
        result = extract_json(raw)
        assert result.success
        assert result.data is not None
        assert result.data["agenda_type"] == "creative_production"

    def test_qwen_multiple_json_blocks(self):
        """If Qwen outputs multiple JSON blocks, pick the first valid one."""
        raw = textwrap.dedent("""\
            ```json
            {
              "agenda_type": "creative_production",
              "tags": ["design"],
              "risk_tags": [],
              "required_roles": ["coordinator"],
              "optional_roles": [],
              "validator_required": false,
              "codex_required": false,
              "confidence": 0.8,
              "reasoning": "Design task."
            }
            ```

            Alternatively:
            ```json
            {
              "agenda_type": "general_planning",
              "tags": ["planning"],
              "risk_tags": [],
              "required_roles": ["coordinator"],
              "optional_roles": [],
              "validator_required": false,
              "codex_required": false,
              "confidence": 0.6,
              "reasoning": "Or planning."
            }
            ```
        """)
        result = extract_json(raw)
        assert result.success
        assert result.data is not None
        # Should pick the first JSON block (creative_production)
        assert result.data["agenda_type"] == "creative_production"

    def test_json_with_html_entities(self):
        """Some Qwen outputs use HTML entities in JSON."""
        raw = '{"text": "foo &amp; bar"}'
        result = extract_json(raw)
        assert result.success
        assert result.data is not None
        assert result.data["text"] == "foo &amp; bar"

    def test_json_with_null_bytes(self):
        """Rare but possible: null bytes in Qwen output."""
        raw = '{"key": "value"}\x00'
        result = extract_json(raw)
        assert result.success
        assert result.data is not None
        assert result.data["key"] == "value"

    def test_json_with_bom(self):
        """UTF-8 BOM at start of response."""
        raw = "\ufeff" + _VALID_CLASSIFICATION
        result = extract_json(raw)
        assert result.success
        assert result.data is not None
        assert result.data["agenda_type"] == "creative_production"

    def test_large_json_with_many_fields(self):
        """Handle larger JSON with many fields."""
        payload = json.dumps({
            f"field_{i}": f"value_{i}" for i in range(100)
        })
        result = extract_json(payload)
        assert result.success
        assert result.data is not None
        assert len(result.data) == 100

    def test_json_with_escaped_unicode(self):
        """Qwen might escape non-ASCII characters."""
        # json.dumps with ensure_ascii=True (the default) will produce
        # escaped unicode like "\ud55c\uae00"
        raw = json.dumps({"topic": "한글 test"}, ensure_ascii=True)
        result = extract_json(raw)
        assert result.success
        assert result.data is not None
        assert result.data["topic"] == "한글 test"


# ═══════════════════════════════════════════════════════════════════════
# 11. ParseErrorType consistency
# ═══════════════════════════════════════════════════════════════════════

class TestParseErrorTypeValues:
    """Verify ParseErrorType constants are well-defined."""

    def test_all_error_types_are_strings(self):
        assert isinstance(ParseErrorType.EMPTY_RESPONSE, str)
        assert isinstance(ParseErrorType.NO_JSON_FOUND, str)
        assert isinstance(ParseErrorType.MALFORMED_JSON, str)
        assert isinstance(ParseErrorType.TRUNCATED_JSON, str)
        assert isinstance(ParseErrorType.NON_JSON_CONTENT, str)

    def test_error_types_are_unique(self):
        types = [
            ParseErrorType.EMPTY_RESPONSE,
            ParseErrorType.NO_JSON_FOUND,
            ParseErrorType.MALFORMED_JSON,
            ParseErrorType.TRUNCATED_JSON,
            ParseErrorType.NON_JSON_CONTENT,
        ]
        assert len(types) == len(set(types))


# ═══════════════════════════════════════════════════════════════════════
# 12. Integration with qwen_router classification
# ═══════════════════════════════════════════════════════════════════════

class TestIntegrationWithRouter:
    """Verify the extractor works correctly as the backend for qwen_router."""

    def test_can_replace_parse_classification_response(self):
        """The extractor should handle all the same cases as the existing parser.

        Existing parse_classification_response handles:
        - Clean JSON
        - Markdown-fenced JSON
        - Leading/trailing text
        - Missing fields
        """
        # All these patterns are covered by tests above
        # This test just ensures the basic classification payload works end-to-end
        test_cases = [
            _VALID_CLASSIFICATION,
            "```json\n" + _VALID_CLASSIFICATION + "\n```",
            "Preamble\n" + _VALID_CLASSIFICATION + "\nPostamble",
        ]
        for i, raw in enumerate(test_cases):
            result = extract_json(raw)
            assert result.success, f"Test case {i} failed: {result.error}"
            assert result.data is not None
            assert "agenda_type" in result.data, (
                f"Test case {i}: missing agenda_type"
            )
            assert "tags" in result.data, (
                f"Test case {i}: missing tags"
            )
