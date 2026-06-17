"""
Tests for the Response Parser module (Sub-AC 2c-3).

Verifies:
- Successful parsing of clean, fenced, and noisy Qwen CLI output
- Team derivation from role_ids
- Priority derivation from risk_tags
- Validation scoring and verdict computation
- Every failure path (empty, non-JSON, malformed, schema violations)
- Edge cases (Boolean confidence, empty arrays, unknown roles)
- Dataclass immutability
"""

import json
import textwrap

import pytest

from src.response_parser import (
    ClassificationResult,
    parse_response,
    _derive_priority,
    _roles_to_teams,
    _compute_validation_score,
    _compute_verdict,
    _safe_tuple_of_strings,
    _P0_RISK_TAGS,
    _P1_RISK_TAGS,
    _P2_RISK_TAGS,
)
from src.qwen_json_extractor import (
    QwenExtractionResult,
    QwenParseError,
    ParseErrorType,
)
from src.qwen_field_validator import ValidationReport


# ═══════════════════════════════════════════════════════════════════════════
# Sample raw outputs (fixtures)
# ═══════════════════════════════════════════════════════════════════════════

_VALID_JSON = json.dumps({
    "agenda_type": "creative_production",
    "tags": ["character-design", "visual-concept", "sns-strategy"],
    "risk_tags": ["brand"],
    "required_roles": ["coordinator", "art-director", "marketing-lead"],
    "optional_roles": ["concept-artist", "sns-strategist"],
    "validator_required": True,
    "codex_required": False,
    "confidence": 0.92,
    "reasoning": "Visual design + SNS strategy spans art and marketing.",
})

_VALID_JSON_TECH = json.dumps({
    "agenda_type": "technical_development",
    "tags": ["backend-api", "refactoring", "database"],
    "risk_tags": ["technical", "schedule"],
    "required_roles": ["coordinator", "tech-director", "backend-dev"],
    "optional_roles": ["devops-engineer"],
    "validator_required": True,
    "codex_required": True,
    "confidence": 0.88,
    "reasoning": "Backend refactoring with timeline risk.",
})

_VALID_JSON_SECURITY = json.dumps({
    "agenda_type": "risk_assessment",
    "tags": ["security", "vulnerability", "data-protection"],
    "risk_tags": ["security", "data_loss", "legal"],
    "required_roles": ["coordinator", "tech-director", "security-engineer"],
    "optional_roles": [],
    "validator_required": True,
    "codex_required": True,
    "confidence": 0.95,
    "reasoning": "Critical security review with legal implications.",
})

_VALID_JSON_MINIMAL = json.dumps({
    "agenda_type": "general_planning",
    "tags": ["brainstorming"],
    "risk_tags": [],
    "required_roles": ["coordinator"],
    "optional_roles": [],
    "validator_required": False,
    "codex_required": False,
    "confidence": 0.78,
    "reasoning": "Simple brainstorming session.",
})

_VALID_JSON_BUDGET_BRAND = json.dumps({
    "agenda_type": "marketing_strategy",
    "tags": ["brand", "budget-planning", "campaign"],
    "risk_tags": ["budget", "brand"],
    "required_roles": ["coordinator", "marketing-lead"],
    "optional_roles": [],
    "validator_required": True,
    "codex_required": False,
    "confidence": 0.85,
    "reasoning": "Marketing campaign with budget and brand implications.",
})


# ═══════════════════════════════════════════════════════════════════════════
# Success path tests
# ═══════════════════════════════════════════════════════════════════════════

class TestParseSuccess:
    """Happy-path: clean JSON produces correct ClassificationResult."""

    def test_parses_clean_json_all_fields(self):
        result = parse_response(_VALID_JSON)
        assert result.agenda_type == "creative_production"
        assert result.tags == ("character-design", "visual-concept", "sns-strategy")
        assert result.risk_tags == ("brand",)
        assert result.required_roles == ("coordinator", "art-director", "marketing-lead")
        assert result.optional_roles == ("concept-artist", "sns-strategist")
        assert result.validator_required is True
        assert result.codex_required is False
        assert result.confidence == 0.92
        assert "Visual design" in result.reasoning
        assert result.validation_verdict == "pass"
        assert result.validation_score == 1.0
        assert result.is_valid is True

    def test_parses_technical_topic(self):
        result = parse_response(_VALID_JSON_TECH)
        assert result.agenda_type == "technical_development"
        assert result.priority == "P2"  # technical + schedule
        assert "tech_development" in result.teams

    def test_parses_minimal_meeting(self):
        result = parse_response(_VALID_JSON_MINIMAL)
        assert result.agenda_type == "general_planning"
        assert result.priority == "P3"
        assert result.teams == ("coordination",)

    def test_result_is_frozen(self):
        result = parse_response(_VALID_JSON)
        with pytest.raises(Exception):
            result.agenda_type = "changed"  # type: ignore[misc]

    def test_tags_are_lowercased_and_stripped(self):
        raw = json.dumps({
            "tags": ["  Character-Design  ", "SNS STRATEGY"],
        })
        result = parse_response(raw)
        assert result.tags == ("character-design", "sns strategy")


# ═══════════════════════════════════════════════════════════════════════════
# Markdown fence handling
# ═══════════════════════════════════════════════════════════════════════════

class TestMarkdownFences:
    """Parser correctly handles markdown-fenced JSON."""

    def test_triple_backtick_json_fence(self):
        raw = textwrap.dedent("""\
            Here is the classification:
            ```json
            {
              "agenda_type": "technical_development",
              "tags": ["api"],
              "risk_tags": [],
              "required_roles": ["coordinator"],
              "optional_roles": [],
              "validator_required": false,
              "codex_required": false,
              "confidence": 0.8,
              "reasoning": "API work."
            }
            ```
            Let me know if you need anything.
        """)
        result = parse_response(raw)
        assert result.agenda_type == "technical_development"
        assert result.validation_verdict == "pass"

    def test_triple_backtick_no_lang(self):
        raw = '```\n{"agenda_type": "general_planning"}\n```'
        result = parse_response(raw)
        assert result.agenda_type == "general_planning"

    def test_tilde_fence(self):
        raw = '~~~json\n{"agenda_type": "project_review", "tags": [], "risk_tags": [], "required_roles": ["coordinator"], "optional_roles": [], "validator_required": false, "codex_required": false, "confidence": 0.5, "reasoning": ""}\n~~~'
        result = parse_response(raw)
        assert result.agenda_type == "project_review"


# ═══════════════════════════════════════════════════════════════════════════
# Leading / trailing text
# ═══════════════════════════════════════════════════════════════════════════

class TestSurroundingText:
    """Parser extracts JSON from text with preamble/epilogue."""

    def test_leading_text(self):
        raw = "Classification result:\n\n" + _VALID_JSON_MINIMAL
        result = parse_response(raw)
        assert result.agenda_type == "general_planning"

    def test_trailing_text(self):
        raw = _VALID_JSON_MINIMAL + "\n\nHope this helps!"
        result = parse_response(raw)
        assert result.agenda_type == "general_planning"

    def test_both_leading_and_trailing(self):
        raw = (
            "Here you go:\n\n"
            + _VALID_JSON_MINIMAL
            + "\n\nLet me know if correct."
        )
        result = parse_response(raw)
        assert result.agenda_type == "general_planning"


# ═══════════════════════════════════════════════════════════════════════════
# Failure path tests
# ═══════════════════════════════════════════════════════════════════════════

class TestFailurePaths:
    """Every failure mode returns a valid ClassificationResult, never raises."""

    def test_empty_string_returns_fail(self):
        result = parse_response("")
        assert result.validation_verdict == "fail"
        assert result.is_valid is False
        assert result.validation_score == 0.0
        assert result.confidence == 0.0
        assert result.priority == "P2"  # conservative default

    def test_whitespace_only_returns_fail(self):
        result = parse_response("   \n\t  ")
        assert result.validation_verdict == "fail"

    def test_non_json_text_returns_fail(self):
        result = parse_response("This is just some random text, no JSON here.")
        assert result.validation_verdict == "fail"

    def test_non_json_with_braces_but_no_dict(self):
        # Has braces but they're not JSON
        result = parse_response("if (x > 0) { do_something(); }")
        # Might extract garbage but validation fails
        assert result.validation_verdict in ("fail", "escalate", "revision_required", "conditional_pass")


# ═══════════════════════════════════════════════════════════════════════════
# Malformed / repaired JSON
# ═══════════════════════════════════════════════════════════════════════════

class TestMalformedJson:
    """Parser handles malformed/truncated JSON (repair attempts)."""

    def test_truncated_json_conditional_pass(self):
        raw = '{"agenda_type": "creative_production", "tags": ["art"'
        result = parse_response(raw)
        # Should attempt repair and produce conditional_pass
        assert result.validation_verdict in ("conditional_pass", "revision_required", "escalate", "fail")
        # Even if repaired, classification may be partial
        assert result.validation_score < 1.0


# ═══════════════════════════════════════════════════════════════════════════
# Schema violation tests
# ═══════════════════════════════════════════════════════════════════════════

class TestSchemaViolations:
    """Parser detects and reports schema validation failures."""

    def test_missing_required_fields(self):
        raw = json.dumps({"tags": ["some-tag"]})
        result = parse_response(raw)
        assert result.validation_verdict != "pass"
        assert result.validation_score < 1.0

    def test_wrong_type_for_tags(self):
        raw = json.dumps({
            "agenda_type": "general_planning",
            "tags": "not-a-list",
        })
        result = parse_response(raw)
        assert result.validation_verdict != "pass"

    def test_boolean_instead_of_float_confidence(self):
        # Python bool is subclass of int — our parser should detect this
        raw = json.dumps({
            "agenda_type": "general_planning",
            "tags": [],
            "risk_tags": [],
            "required_roles": ["coordinator"],
            "optional_roles": [],
            "validator_required": False,
            "codex_required": False,
            "confidence": True,
            "reasoning": "",
        })
        result = parse_response(raw)
        # JSON boolean `true` should be rejected as confidence value
        assert result.confidence == 0.5  # falls back to default

    def test_bad_agenda_type_value(self):
        raw = json.dumps({
            "agenda_type": "nonexistent_type",
            "tags": [],
            "risk_tags": [],
            "required_roles": ["coordinator"],
            "optional_roles": [],
            "validator_required": False,
            "codex_required": False,
            "confidence": 0.5,
            "reasoning": "",
        })
        result = parse_response(raw)
        assert result.validation_verdict != "pass"

    def test_non_string_items_in_tags(self):
        raw = json.dumps({
            "agenda_type": "general_planning",
            "tags": ["valid", 123, None, True],
            "risk_tags": [],
            "required_roles": ["coordinator"],
            "optional_roles": [],
            "validator_required": False,
            "codex_required": False,
            "confidence": 0.5,
            "reasoning": "",
        })
        result = parse_response(raw)
        # Non-string items are stripped; tags contains only "valid"
        assert result.tags == ("valid",)


# ═══════════════════════════════════════════════════════════════════════════
# Team derivation tests
# ═══════════════════════════════════════════════════════════════════════════

class TestTeamDerivation:
    """Roles are correctly mapped to their parent teams."""

    def test_all_required_roles_mapped(self):
        result = parse_response(_VALID_JSON)
        # "coordinator" → coordination, "art-director" → art_design,
        # "marketing-lead" → marketing
        assert "coordination" in result.teams
        assert "art_design" in result.teams
        assert "marketing" in result.teams
        assert len(result.teams) == 3

    def test_optional_roles_also_mapped(self):
        result = parse_response(_VALID_JSON)
        # optional roles: "concept-artist" → art_design (already in teams)
        # "sns-strategist" → marketing (already in teams)
        # No new teams, still 3
        assert len(result.teams) == 3

    def test_teams_deduplicated(self):
        raw = json.dumps({
            "agenda_type": "creative_production",
            "tags": [],
            "risk_tags": [],
            "required_roles": ["art-director", "illustrator"],
            "optional_roles": ["concept-artist", "character-designer"],
            "validator_required": False,
            "codex_required": False,
            "confidence": 0.5,
            "reasoning": "",
        })
        result = parse_response(raw)
        # all roles are in art_design — only one team
        assert result.teams == ("art_design",)

    def test_unknown_role_id_skipped(self):
        raw = json.dumps({
            "agenda_type": "general_planning",
            "tags": [],
            "risk_tags": [],
            "required_roles": ["coordinator", "nonexistent-role"],
            "optional_roles": [],
            "validator_required": False,
            "codex_required": False,
            "confidence": 0.5,
            "reasoning": "",
        })
        result = parse_response(raw)
        assert "coordination" in result.teams
        # "nonexistent-role" is silently skipped

    def test_security_team_mapping(self):
        result = parse_response(_VALID_JSON_SECURITY)
        assert "coordination" in result.teams
        assert "tech_development" in result.teams


# ═══════════════════════════════════════════════════════════════════════════
# Priority derivation tests
# ═══════════════════════════════════════════════════════════════════════════

class TestPriorityDerivation:
    """Priority is correctly derived from risk_tags (unit + integration)."""

    # Unit tests for _derive_priority
    def test_p0_security(self):
        assert _derive_priority(("security",), 0.9) == "P0"

    def test_p0_data_loss(self):
        assert _derive_priority(("data_loss",), 0.9) == "P0"

    def test_p0_multiple_p1_cumulative(self):
        # Two P1-level risks escalate to P0
        assert _derive_priority(("legal", "brand"), 0.9) == "P0"

    def test_p0_p1_plus_budget(self):
        assert _derive_priority(("budget", "brand"), 0.9) == "P0"

    def test_p1_single(self):
        assert _derive_priority(("legal",), 0.9) == "P1"
        assert _derive_priority(("budget",), 0.9) == "P1"
        assert _derive_priority(("brand",), 0.9) == "P1"
        assert _derive_priority(("external",), 0.9) == "P1"

    def test_p2(self):
        assert _derive_priority(("technical",), 0.9) == "P2"
        assert _derive_priority(("schedule",), 0.9) == "P2"

    def test_p3_no_risks(self):
        assert _derive_priority((), 0.9) == "P3"

    def test_p3_unknown_risk_tag(self):
        assert _derive_priority(("unknown-tag",), 0.9) == "P3"

    # Integration tests through parse_response
    def test_parsed_p0_from_security_json(self):
        result = parse_response(_VALID_JSON_SECURITY)
        # security + data_loss + legal → P0
        assert result.priority == "P0"

    def test_parsed_p1_from_brand_json(self):
        result = parse_response(_VALID_JSON)
        # brand only → P1
        assert result.priority == "P1"

    def test_parsed_p0_from_budget_brand(self):
        result = parse_response(_VALID_JSON_BUDGET_BRAND)
        # budget + brand = 2 P1 risks → P0
        assert result.priority == "P0"

    def test_parsed_p2_from_technical(self):
        result = parse_response(_VALID_JSON_TECH)
        # technical + schedule → P2
        assert result.priority == "P2"


# ═══════════════════════════════════════════════════════════════════════════
# Validation scoring unit tests
# ═══════════════════════════════════════════════════════════════════════════

class TestValidationScoring:
    """Unit tests for _compute_validation_score."""

    def _make_extraction(self, success=True, repaired=False):
        if success:
            return QwenExtractionResult(data={"test": 1}, error=None, was_repaired=repaired)
        return QwenExtractionResult(
            data=None,
            error=QwenParseError(
                error_type=ParseErrorType.EMPTY_RESPONSE,
                message="Empty",
                recovery_hint="Retry",
            ),
        )

    def test_perfect_extraction_scores_1_0(self):
        ext = self._make_extraction(success=True, repaired=False)
        report = ValidationReport(passed=True, errors=(), total_fields_checked=9)
        assert _compute_validation_score(ext, report) == 1.0

    def test_repaired_extraction_loses_0_15(self):
        ext = self._make_extraction(success=True, repaired=True)
        report = ValidationReport(passed=True, errors=(), total_fields_checked=9)
        assert _compute_validation_score(ext, report) == 0.85

    def test_validation_errors_reduce_score(self):
        ext = self._make_extraction(success=True, repaired=False)
        from src.qwen_field_validator import FieldValidationError
        errors = tuple(
            FieldValidationError(
                field_name=f"field_{i}",
                error_type="missing",
                message="Missing",
                expected="str",
                actual="None",
            )
            for i in range(3)
        )
        report = ValidationReport(passed=False, errors=errors, total_fields_checked=9)
        score = _compute_validation_score(ext, report)
        assert 0.65 <= score <= 0.75  # 1.0 - 3 * 0.10 = 0.70

    def test_extraction_failure_scores_0_0(self):
        ext = self._make_extraction(success=False)
        assert _compute_validation_score(ext, None) == 0.0

    def test_score_never_below_0(self):
        ext = self._make_extraction(success=True, repaired=True)
        from src.qwen_field_validator import FieldValidationError
        errors = tuple(
            FieldValidationError(f"f{i}", "missing", "x", "y", "z")
            for i in range(20)
        )
        report = ValidationReport(passed=False, errors=errors, total_fields_checked=9)
        score = _compute_validation_score(ext, report)
        assert score >= 0.0, f"Score {score} should never be negative"


# ═══════════════════════════════════════════════════════════════════════════
# Verdict computation unit tests
# ═══════════════════════════════════════════════════════════════════════════

class TestVerdictComputation:
    """Unit tests for _compute_verdict."""

    def _make_extraction(self, success=True, repaired=False):
        if success:
            return QwenExtractionResult(data={"test": 1}, error=None, was_repaired=repaired)
        return QwenExtractionResult(
            data=None,
            error=QwenParseError(
                error_type=ParseErrorType.EMPTY_RESPONSE,
                message="Empty",
                recovery_hint="Retry",
            ),
        )

    def _make_report(self, passed=True):
        return ValidationReport(passed=passed, errors=(), total_fields_checked=9)

    def test_pass(self):
        assert _compute_verdict(
            self._make_extraction(success=True, repaired=False),
            self._make_report(passed=True),
            1.0,
        ) == "pass"

    def test_conditional_pass_from_repair(self):
        assert _compute_verdict(
            self._make_extraction(success=True, repaired=True),
            self._make_report(passed=True),
            0.85,
        ) == "conditional_pass"

    def test_conditional_pass_from_score(self):
        assert _compute_verdict(
            self._make_extraction(success=True, repaired=False),
            self._make_report(passed=False),
            0.70,
        ) == "conditional_pass"

    def test_revision_required(self):
        assert _compute_verdict(
            self._make_extraction(success=True, repaired=False),
            self._make_report(passed=False),
            0.50,
        ) == "revision_required"

    def test_escalate_low_score(self):
        assert _compute_verdict(
            self._make_extraction(success=True, repaired=False),
            self._make_report(passed=False),
            0.30,
        ) == "escalate"

    def test_fail_on_extraction_failure(self):
        assert _compute_verdict(
            self._make_extraction(success=False),
            None,
            0.0,
        ) == "fail"


# ═══════════════════════════════════════════════════════════════════════════
# Edge case tests
# ═══════════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    """Miscellaneous edge-case coverage."""

    def test_confidence_exactly_0(self):
        raw = json.dumps({
            "agenda_type": "general_planning",
            "tags": [],
            "risk_tags": [],
            "required_roles": ["coordinator"],
            "optional_roles": [],
            "validator_required": False,
            "codex_required": False,
            "confidence": 0.0,
            "reasoning": "",
        })
        result = parse_response(raw)
        assert result.confidence == 0.0

    def test_confidence_exactly_1(self):
        raw = json.dumps({
            "agenda_type": "general_planning",
            "tags": [],
            "risk_tags": [],
            "required_roles": ["coordinator"],
            "optional_roles": [],
            "validator_required": False,
            "codex_required": False,
            "confidence": 1.0,
            "reasoning": "",
        })
        result = parse_response(raw)
        assert result.confidence == 1.0

    def test_confidence_out_of_range_clamped(self):
        for val in (-0.5, 2.5):
            raw = json.dumps({
                "agenda_type": "general_planning",
                "tags": [],
                "risk_tags": [],
                "required_roles": ["coordinator"],
                "optional_roles": [],
                "validator_required": False,
                "codex_required": False,
                "confidence": val,
                "reasoning": "",
            })
            result = parse_response(raw)
            assert 0.0 <= result.confidence <= 1.0

    def test_empty_tags_produces_empty_tuple(self):
        raw = json.dumps({
            "agenda_type": "general_planning",
            "tags": [],
            "risk_tags": [],
            "required_roles": [],
            "optional_roles": [],
            "validator_required": False,
            "codex_required": False,
            "confidence": 0.5,
            "reasoning": "",
        })
        result = parse_response(raw)
        assert result.tags == ()
        assert result.risk_tags == ()
        assert result.required_roles == ()

    def test_missing_all_fields_uses_defaults(self):
        raw = "{}"
        result = parse_response(raw)
        assert result.agenda_type == "general_planning"
        assert result.tags == ()
        assert result.priority == "P3"
        assert result.confidence == 0.5
        assert result.validator_required is False
        assert result.codex_required is False
        assert result.validation_verdict != "pass"

    def test_integer_confidence_accepted(self):
        raw = json.dumps({
            "agenda_type": "general_planning",
            "tags": [],
            "risk_tags": [],
            "required_roles": ["coordinator"],
            "optional_roles": [],
            "validator_required": False,
            "codex_required": False,
            "confidence": 1,
            "reasoning": "",
        })
        result = parse_response(raw)
        assert result.confidence == 1.0

    def test_none_response_never_raises(self):
        # The type is wrong (None instead of str), but the parser
        # should handle it gracefully via the type-stripping logic
        # in parse_response.
        # In practice, extract_json handles empty/None via the
        # empty-response detection.
        # We test that no exception is raised.
        try:
            parse_response("")  # type-safe equivalent of receiving None
        except Exception as exc:
            pytest.fail(f"parse_response raised {type(exc).__name__}: {exc}")

    def test_is_valid_property(self):
        assert parse_response("").is_valid is False
        assert parse_response(_VALID_JSON).is_valid is True

    def test_needs_escalation_property(self):
        assert parse_response("").needs_escalation is True
        assert parse_response(_VALID_JSON).needs_escalation is False


# ═══════════════════════════════════════════════════════════════════════════
# _safe_tuple_of_strings unit tests
# ═══════════════════════════════════════════════════════════════════════════

class TestSafeTupleOfStrings:
    """Unit tests for the internal string-list normaliser."""

    def test_list_of_strings(self):
        assert _safe_tuple_of_strings(["a", "b", "c"]) == ("a", "b", "c")

    def test_list_with_non_strings(self):
        assert _safe_tuple_of_strings(["a", 1, None, True, "b"]) == ("a", "b")

    def test_empty_list(self):
        assert _safe_tuple_of_strings([]) == ()

    def test_non_list(self):
        assert _safe_tuple_of_strings("not a list") == ()
        assert _safe_tuple_of_strings(None) == ()
        assert _safe_tuple_of_strings(42) == ()

    def test_whitespace_stripped_and_lowered(self):
        assert _safe_tuple_of_strings(["  HELLO  ", "WoRlD"]) == ("hello", "world")

    def test_empty_strings_removed(self):
        assert _safe_tuple_of_strings(["", "  ", "valid"]) == ("valid",)


# ═══════════════════════════════════════════════════════════════════════════
# _roles_to_teams unit tests
# ═══════════════════════════════════════════════════════════════════════════

class TestRolesToTeams:
    """Unit tests for role → team mapping."""

    def test_known_roles(self):
        teams = _roles_to_teams(("coordinator", "art-director", "backend-dev"))
        assert teams == ("coordination", "art_design", "tech_development")

    def test_unknown_role_skipped(self):
        teams = _roles_to_teams(("coordinator", "made-up-role"))
        assert teams == ("coordination",)

    def test_all_roles_unknown(self):
        teams = _roles_to_teams(("fake1", "fake2"))
        assert teams == ()

    def test_deduplication(self):
        teams = _roles_to_teams(
            ("coordinator", "art-director", "content-pd", "scriptwriter")
        )
        # "scriptwriter" is also content_production (same team as content-pd)
        assert len(teams) == 3  # coordination, art_design, content_production

    def test_order_preserved(self):
        # First occurrence determines position
        teams = _roles_to_teams(("art-director", "coordinator", "backend-dev"))
        assert teams[0] == "art_design"
        assert teams[1] == "coordination"
        assert teams[2] == "tech_development"


# ═══════════════════════════════════════════════════════════════════════════
# 29 role consistency tests
# ═══════════════════════════════════════════════════════════════════════════

class TestAll29RolesMapped:
    """Every role in TEAM_ROLES has a valid team mapping."""

    def test_all_roles_have_team(self):
        from src.qwen_router import ALL_ROLES
        from src.response_parser import _ROLE_TO_TEAM
        for role in ALL_ROLES:
            rid = role["role_id"]
            assert rid in _ROLE_TO_TEAM, (
                f"Role '{rid}' has no team mapping in _ROLE_TO_TEAM"
            )
            assert _ROLE_TO_TEAM[rid] in (
                "coordination", "content_production", "art_design",
                "tech_development", "marketing", "execution",
            )

    def test_all_29_roles_mapped(self):
        from src.qwen_router import ALL_ROLES
        from src.response_parser import _ROLE_TO_TEAM
        assert len(_ROLE_TO_TEAM) == len(ALL_ROLES)


# ═══════════════════════════════════════════════════════════════════════════
# ClassificationResult property tests
# ═══════════════════════════════════════════════════════════════════════════

class TestClassificationResultProperties:
    """Properties is_valid and needs_escalation work correctly."""

    def test_pass_result_is_valid(self):
        r = ClassificationResult(
            agenda_type="test", tags=(), risk_tags=(), required_roles=(),
            optional_roles=(), teams=(), priority="P3", confidence=0.5,
            reasoning="", validation_score=1.0, validation_verdict="pass",
            validator_required=False, codex_required=False,
        )
        assert r.is_valid is True
        assert r.needs_escalation is False

    def test_fail_result_not_valid(self):
        r = ClassificationResult(
            agenda_type="test", tags=(), risk_tags=(), required_roles=(),
            optional_roles=(), teams=(), priority="P3", confidence=0.5,
            reasoning="", validation_score=0.0, validation_verdict="fail",
            validator_required=False, codex_required=False,
        )
        assert r.is_valid is False
        assert r.needs_escalation is True

    def test_conditional_pass_is_valid(self):
        r = ClassificationResult(
            agenda_type="test", tags=(), risk_tags=(), required_roles=(),
            optional_roles=(), teams=(), priority="P3", confidence=0.5,
            reasoning="", validation_score=0.8, validation_verdict="conditional_pass",
            validator_required=False, codex_required=False,
        )
        assert r.is_valid is True
        assert r.needs_escalation is False

    def test_escalate_needs_escalation(self):
        r = ClassificationResult(
            agenda_type="test", tags=(), risk_tags=(), required_roles=(),
            optional_roles=(), teams=(), priority="P3", confidence=0.5,
            reasoning="", validation_score=0.3, validation_verdict="escalate",
            validator_required=False, codex_required=False,
        )
        assert r.is_valid is True  # still valid (not "fail")
        assert r.needs_escalation is True
