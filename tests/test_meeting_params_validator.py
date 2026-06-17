"""Tests for meeting parameter validator (Sub-AC 1b-ii).

Verifies that ``validate_meeting_params()``:
- Accepts valid topic, team_selection, urgency combinations
- Rejects empty/whitespace-only topics
- Enforces topic length limits
- Validates urgency against recognised tiers (p0-p3)
- Validates team_selection against recognised team IDs
- Deduplicates and sorts team_selection
- Collects all errors before returning (no fail-fast)
- Applies default urgency p2 when not provided
- Handles edge cases: Korean text, None team_selection, empty strings
- Produces correctly structured MeetingParamsResult / MeetingParams objects

Test categories:
1. Happy path — valid inputs with various combinations
2. Topic validation — empty, whitespace, too-long
3. Urgency validation — valid tiers, invalid, empty→default, case insensitivity
4. Team selection validation — valid IDs, invalid, duplicates, non-strings
5. Multiple simultaneous errors
6. Edge cases — None team_selection, Korean text, whitespace handling
7. MeetingParams dataclass — direct construction validation
8. Type guards and helper functions
9. Serialization (to_dict)
"""

from __future__ import annotations

import pytest

from src.meeting_params_validator import (
    MAX_TOPIC_LENGTH,
    VALID_TEAM_IDS,
    VALID_URGENCY_TIERS,
    MeetingParams,
    MeetingParamsError,
    MeetingParamsResult,
    format_params_errors,
    is_params_error,
    is_params_success,
    validate_meeting_params,
)


# ═══════════════════════════════════════════════════════════════════════════
# 1. Happy path — valid inputs
# ═══════════════════════════════════════════════════════════════════════════


class TestValidParams:
    """Valid input combinations produce successful results."""

    def test_minimal_input_topic_only(self):
        """Only required field (topic) with defaults for everything else."""
        result = validate_meeting_params("신규 캐릭터 디자인 검토")
        assert result.success
        assert result.params is not None
        assert result.params.topic == "신규 캐릭터 디자인 검토"
        assert result.params.urgency == "p2"
        assert result.params.team_selection == ()

    def test_topic_with_urgency(self):
        """Topic with explicit valid urgency."""
        result = validate_meeting_params(
            "뮤직비디오 오프닝 아이디어 회의",
            urgency="p1",
        )
        assert result.success
        assert result.params.topic == "뮤직비디오 오프닝 아이디어 회의"
        assert result.params.urgency == "p1"

    def test_topic_with_team_selection(self):
        """Topic with valid team selection."""
        result = validate_meeting_params(
            "API 설계 논의",
            team_selection=["tech-engineering", "art-design"],
        )
        assert result.success
        assert result.params.topic == "API 설계 논의"
        assert result.params.team_selection == ("art-design", "tech-engineering")

    def test_all_fields_provided(self):
        """All three fields provided with valid values."""
        result = validate_meeting_params(
            topic="마케팅 캠페인 기획",
            team_selection=["marketing", "content-production", "art-design"],
            urgency="p0",
        )
        assert result.success
        assert result.params.topic == "마케팅 캠페인 기획"
        assert result.params.urgency == "p0"
        assert result.params.team_selection == (
            "art-design",
            "content-production",
            "marketing",
        )

    def test_english_topic(self):
        """English topic works."""
        result = validate_meeting_params("Q3 planning and budgeting")
        assert result.success
        assert result.params.topic == "Q3 planning and budgeting"

    def test_topic_with_whitespace_trimmed(self):
        """Leading/trailing whitespace is stripped from topic."""
        result = validate_meeting_params("   회의 안건   ")
        assert result.success
        assert result.params.topic == "회의 안건"

    def test_all_six_teams_accepted(self):
        """Every team ID in VALID_TEAM_IDS is accepted."""
        all_teams = sorted(VALID_TEAM_IDS)
        result = validate_meeting_params(
            "전체 팀 회의",
            team_selection=all_teams,
        )
        assert result.success
        assert result.params.team_selection == tuple(all_teams)

    def test_none_team_selection_means_no_filter(self):
        """None team_selection produces empty tuple."""
        result = validate_meeting_params("test", team_selection=None)
        assert result.success
        assert result.params.team_selection == ()


class TestUrgencyTiers:
    """All four urgency tiers are accepted."""

    @pytest.mark.parametrize("tier", ["p0", "p1", "p2", "p3"])
    def test_valid_urgency_tier(self, tier):
        result = validate_meeting_params("test", urgency=tier)
        assert result.success
        assert result.params.urgency == tier

    @pytest.mark.parametrize("tier", ["P0", "P1", "P2", "P3"])
    def test_urgency_case_insensitive(self, tier):
        """Urgency is lowercased during validation."""
        result = validate_meeting_params("test", urgency=tier)
        assert result.success
        assert result.params.urgency == tier.lower()

    def test_urgency_with_whitespace(self):
        """Whitespace around urgency is stripped."""
        result = validate_meeting_params("test", urgency="  p1  ")
        assert result.success
        assert result.params.urgency == "p1"


# ═══════════════════════════════════════════════════════════════════════════
# 2. Topic validation — empty, whitespace, too-long
# ═══════════════════════════════════════════════════════════════════════════


class TestTopicValidation:
    """Topic rejection cases."""

    def test_empty_topic_rejected(self):
        result = validate_meeting_params("")
        assert not result.success
        assert len(result.errors) >= 1
        topic_errors = [e for e in result.errors if e.field == "topic"]
        assert len(topic_errors) == 1
        assert topic_errors[0].code == "EMPTY_TOPIC"

    def test_whitespace_only_topic_rejected(self):
        result = validate_meeting_params("   \t\n  ")
        assert not result.success
        topic_errors = [e for e in result.errors if e.field == "topic"]
        assert len(topic_errors) == 1
        assert topic_errors[0].code == "EMPTY_TOPIC"

    def test_topic_too_long_rejected(self):
        long_topic = "X" * (MAX_TOPIC_LENGTH + 1)
        result = validate_meeting_params(long_topic)
        assert not result.success
        topic_errors = [e for e in result.errors if e.field == "topic"]
        assert len(topic_errors) == 1
        assert topic_errors[0].code == "TOPIC_TOO_LONG"
        assert str(MAX_TOPIC_LENGTH) in topic_errors[0].message

    def test_topic_at_max_length_accepted(self):
        """Topic exactly at max length should pass."""
        topic = "X" * MAX_TOPIC_LENGTH
        result = validate_meeting_params(topic)
        assert result.success
        assert len(result.params.topic) == MAX_TOPIC_LENGTH

    def test_topic_one_char_accepted(self):
        """Single character topic is valid."""
        result = validate_meeting_params("X")
        assert result.success
        assert result.params.topic == "X"


# ═══════════════════════════════════════════════════════════════════════════
# 3. Urgency validation
# ═══════════════════════════════════════════════════════════════════════════


class TestUrgencyValidation:
    """Urgency rejection cases."""

    def test_invalid_urgency_rejected(self):
        result = validate_meeting_params("test", urgency="p5")
        assert not result.success
        urgency_errors = [e for e in result.errors if e.field == "urgency"]
        assert len(urgency_errors) == 1
        assert urgency_errors[0].code == "INVALID_URGENCY"

    def test_nonsense_urgency_rejected(self):
        result = validate_meeting_params("test", urgency="critical")
        assert not result.success
        assert any(e.code == "INVALID_URGENCY" for e in result.errors)

    def test_empty_urgency_defaults_to_p2(self):
        """Empty urgency string defaults to p2 (not an error)."""
        result = validate_meeting_params("test", urgency="")
        assert result.success
        assert result.params.urgency == "p2"

    def test_whitespace_urgency_defaults_to_p2(self):
        """Whitespace-only urgency defaults to p2."""
        result = validate_meeting_params("test", urgency="   ")
        assert result.success
        assert result.params.urgency == "p2"

    def test_numeric_urgency_rejected(self):
        """Numbers not a valid urgency string."""
        result = validate_meeting_params("test", urgency="123")
        assert not result.success
        assert any(e.code == "INVALID_URGENCY" for e in result.errors)


# ═══════════════════════════════════════════════════════════════════════════
# 4. Team selection validation
# ═══════════════════════════════════════════════════════════════════════════


class TestTeamSelectionValidation:
    """Team selection acceptance and rejection cases."""

    def test_empty_team_list_accepted(self):
        """Empty list means no filter."""
        result = validate_meeting_params("test", team_selection=[])
        assert result.success
        assert result.params.team_selection == ()

    def test_invalid_team_id_rejected(self):
        result = validate_meeting_params(
            "test",
            team_selection=["invalid-team"],
        )
        assert not result.success
        team_errors = [e for e in result.errors if e.field == "team_selection"]
        assert any(e.code == "INVALID_TEAM_ID" for e in team_errors)

    def test_mixed_valid_and_invalid_teams(self):
        """Some valid, some invalid — errors for invalid only."""
        result = validate_meeting_params(
            "test",
            team_selection=["art-design", "fake-team", "marketing", "also-fake"],
        )
        assert not result.success
        team_errors = [e for e in result.errors if e.field == "team_selection"]
        invalid_codes = [e for e in team_errors if e.code == "INVALID_TEAM_ID"]
        assert len(invalid_codes) == 2

    def test_duplicate_teams_collapsed(self):
        """Duplicate entries are deduplicated."""
        result = validate_meeting_params(
            "test",
            team_selection=["art-design", "art-design", "art-design"],
        )
        assert result.success
        assert result.params.team_selection == ("art-design",)

    def test_duplicates_across_case_variations(self):
        """Case differences are normalised then deduplicated."""
        result = validate_meeting_params(
            "test",
            team_selection=["Art-Design", "art-design", "ART-DESIGN"],
        )
        assert result.success
        assert result.params.team_selection == ("art-design",)

    def test_teams_sorted_in_output(self):
        """Output team_selection is alphabetically sorted."""
        result = validate_meeting_params(
            "test",
            team_selection=["execution", "art-design", "content-production"],
        )
        assert result.success
        assert result.params.team_selection == (
            "art-design",
            "content-production",
            "execution",
        )

    def test_empty_string_team_entry_rejected(self):
        result = validate_meeting_params(
            "test",
            team_selection=[""],
        )
        assert not result.success
        team_errors = [e for e in result.errors if e.field == "team_selection"]
        assert any(e.code == "EMPTY_TEAM_ENTRY" for e in team_errors)

    def test_whitespace_only_team_entry_rejected(self):
        result = validate_meeting_params(
            "test",
            team_selection=["   "],
        )
        assert not result.success
        team_errors = [e for e in result.errors if e.field == "team_selection"]
        assert any(e.code == "EMPTY_TEAM_ENTRY" for e in team_errors)

    def test_whitespace_around_team_id_trimmed(self):
        """Whitespace around valid team IDs is stripped."""
        result = validate_meeting_params(
            "test",
            team_selection=["  art-design  ", "\ttech-engineering\n"],
        )
        assert result.success
        assert result.params.team_selection == ("art-design", "tech-engineering")

    def test_non_string_team_entry_rejected(self):
        result = validate_meeting_params(
            "test",
            team_selection=[123, True],  # type: ignore[list-item]
        )
        assert not result.success
        team_errors = [e for e in result.errors if e.field == "team_selection"]
        assert any(e.code == "INVALID_TEAM_TYPE" for e in team_errors)
        assert len(team_errors) == 2  # both entries invalid

    def test_valid_team_ids_in_korean_context(self):
        """Korean meeting with specific teams."""
        result = validate_meeting_params(
            "일러스트 컨셉 회의",
            team_selection=["art-design", "content-production"],
        )
        assert result.success
        assert result.params.team_selection == ("art-design", "content-production")


# ═══════════════════════════════════════════════════════════════════════════
# 5. Multiple simultaneous errors
# ═══════════════════════════════════════════════════════════════════════════


class TestMultipleErrors:
    """All validation errors are collected, not fail-fast."""

    def test_all_fields_invalid(self):
        """Topic empty, urgency invalid, team invalid — all reported."""
        result = validate_meeting_params(
            topic="",
            urgency="urgent",
            team_selection=["nonexistent"],
        )
        assert not result.success
        codes = {e.code for e in result.errors}
        assert "EMPTY_TOPIC" in codes
        assert "INVALID_URGENCY" in codes
        assert "INVALID_TEAM_ID" in codes

    def test_topic_empty_and_urgency_invalid(self):
        """Two errors: topic and urgency."""
        result = validate_meeting_params(topic="", urgency="p5")
        assert not result.success
        assert len(result.errors) == 2
        codes = {e.code for e in result.errors}
        assert codes == {"EMPTY_TOPIC", "INVALID_URGENCY"}

    def test_topic_empty_and_team_invalid(self):
        """Two errors: topic and team_selection."""
        result = validate_meeting_params(
            topic="",
            team_selection=["bad-team"],
        )
        assert not result.success
        codes = {e.code for e in result.errors}
        assert "EMPTY_TOPIC" in codes
        assert "INVALID_TEAM_ID" in codes

    def test_urgency_invalid_and_team_invalid(self):
        """Two errors: urgency and team_selection; topic is fine."""
        result = validate_meeting_params(
            topic="valid topic",
            urgency="p9",
            team_selection=["ghost-team"],
        )
        assert not result.success
        codes = {e.code for e in result.errors}
        assert codes == {"INVALID_URGENCY", "INVALID_TEAM_ID"}


# ═══════════════════════════════════════════════════════════════════════════
# 6. Edge cases
# ═══════════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    """Edge cases and unusual input combinations."""

    def test_korean_topic_with_emoji(self):
        """Korean text with emoji passes through."""
        result = validate_meeting_params("🎬 신규 콘텐츠 기획 회의 ✨")
        assert result.success
        assert "🎬" in result.params.topic

    def test_topic_with_newlines(self):
        """Multi-line topics are preserved."""
        result = validate_meeting_params("Line 1\nLine 2\nLine 3")
        assert result.success
        assert result.params.topic == "Line 1\nLine 2\nLine 3"

    def test_topic_with_special_characters(self):
        """Special characters in topic are fine."""
        result = validate_meeting_params("Budget: $100K ± 5% — Q4 targets")
        assert result.success
        assert result.params.topic == "Budget: $100K ± 5% — Q4 targets"

    def test_only_team_selection_provided_explicit_none(self):
        """Explicit None for team_selection."""
        result = validate_meeting_params("test", team_selection=None)
        assert result.success
        assert result.params.team_selection == ()

    def test_urgency_p0_accepted(self):
        """P0 (blocking) is a valid tier."""
        result = validate_meeting_params("Emergency meeting", urgency="p0")
        assert result.success
        assert result.params.urgency == "p0"

    def test_urgency_p3_accepted(self):
        """P3 (low) is a valid tier."""
        result = validate_meeting_params("Low priority task", urgency="p3")
        assert result.success
        assert result.params.urgency == "p3"

    def test_team_selection_none_and_urgency_default(self):
        """All defaults — minimal valid call."""
        result = validate_meeting_params("minimal")
        assert result.success
        assert result.params.topic == "minimal"
        assert result.params.urgency == "p2"
        assert result.params.team_selection == ()


# ═══════════════════════════════════════════════════════════════════════════
# 7. MeetingParams dataclass — direct construction validation
# ═══════════════════════════════════════════════════════════════════════════


class TestMeetingParamsDataclass:
    """MeetingParams validates on direct construction too."""

    def test_valid_construction(self):
        params = MeetingParams(topic="test", urgency="p1")
        assert params.topic == "test"
        assert params.urgency == "p1"

    def test_empty_topic_raises(self):
        with pytest.raises(ValueError, match="topic must not be empty"):
            MeetingParams(topic="")

    def test_invalid_urgency_raises(self):
        with pytest.raises(ValueError, match="invalid urgency"):
            MeetingParams(topic="test", urgency="invalid")

    def test_invalid_team_raises(self):
        with pytest.raises(ValueError, match="invalid team_id"):
            MeetingParams(topic="test", team_selection=("bad-team",))

    def test_immutable(self):
        """Frozen dataclass cannot be mutated."""
        params = MeetingParams(topic="test")
        with pytest.raises(Exception):
            params.topic = "new"  # type: ignore[misc]

    def test_to_dict(self):
        params = MeetingParams(
            topic="회의 안건",
            team_selection=("art-design", "tech-engineering"),
            urgency="p1",
        )
        d = params.to_dict()
        assert d["topic"] == "회의 안건"
        assert d["urgency"] == "p1"
        assert d["team_selection"] == ["art-design", "tech-engineering"]

    def test_to_dict_empty_team_selection(self):
        params = MeetingParams(topic="test")
        d = params.to_dict()
        assert d["team_selection"] == []


# ═══════════════════════════════════════════════════════════════════════════
# 8. Type guards and helper functions
# ═══════════════════════════════════════════════════════════════════════════


class TestTypeGuards:
    """is_params_success and is_params_error type guards."""

    def test_is_params_success_returns_true_for_success(self):
        result = validate_meeting_params("valid topic")
        assert is_params_success(result) is True

    def test_is_params_success_returns_false_for_error(self):
        result = validate_meeting_params("")
        assert is_params_success(result) is False

    def test_is_params_error_returns_true_for_error(self):
        result = validate_meeting_params("")
        assert is_params_error(result) is True

    def test_is_params_error_returns_false_for_success(self):
        result = validate_meeting_params("valid topic")
        assert is_params_error(result) is False


class TestErrorFormatting:
    """format_params_errors produces human-readable output."""

    def test_format_empty_for_success(self):
        result = validate_meeting_params("valid")
        assert format_params_errors(result) == ""

    def test_format_includes_codes_and_fields(self):
        result = validate_meeting_params("", urgency="p5")
        formatted = format_params_errors(result)
        assert "[EMPTY_TOPIC]" in formatted
        assert "topic:" in formatted
        assert "[INVALID_URGENCY]" in formatted
        assert "urgency:" in formatted

    def test_format_multiline_for_multiple_errors(self):
        result = validate_meeting_params(
            topic="",
            team_selection=["bad"],
        )
        formatted = format_params_errors(result)
        lines = formatted.split("\n")
        assert len(lines) >= 2


# ═══════════════════════════════════════════════════════════════════════════
# 9. Result dataclass structure
# ═══════════════════════════════════════════════════════════════════════════


class TestMeetingParamsResultStructure:
    """MeetingParamsResult has correct structure."""

    def test_success_result_has_no_errors(self):
        result = validate_meeting_params("test")
        assert result.success is True
        assert result.params is not None
        assert result.errors == ()

    def test_error_result_has_no_params(self):
        result = validate_meeting_params("")
        assert result.success is False
        assert result.params is None
        assert len(result.errors) >= 1

    def test_each_error_has_required_fields(self):
        result = validate_meeting_params("", urgency="p5")
        for err in result.errors:
            assert isinstance(err.code, str) and err.code
            assert isinstance(err.message, str) and err.message
            assert isinstance(err.field, str) and err.field

    def test_error_field_matches_failing_parameter(self):
        result = validate_meeting_params("valid", urgency="p5")
        urgency_err = [e for e in result.errors if e.field == "urgency"][0]
        assert urgency_err.code == "INVALID_URGENCY"

    def test_result_is_frozen(self):
        result = validate_meeting_params("test")
        with pytest.raises(Exception):
            result.success = False  # type: ignore[misc]


# ═══════════════════════════════════════════════════════════════════════════
# 10. Constants sanity checks
# ═══════════════════════════════════════════════════════════════════════════


class TestConstants:
    """Ensure constants are well-formed."""

    def test_valid_team_ids_is_frozenset(self):
        assert isinstance(VALID_TEAM_IDS, frozenset)

    def test_valid_team_ids_has_six_entries(self):
        assert len(VALID_TEAM_IDS) == 6

    def test_valid_urgency_tiers_has_four_entries(self):
        assert len(VALID_URGENCY_TIERS) == 4
        assert VALID_URGENCY_TIERS == frozenset({"p0", "p1", "p2", "p3"})

    def test_max_topic_length_is_reasonable(self):
        assert MAX_TOPIC_LENGTH > 0
        assert MAX_TOPIC_LENGTH >= 2000  # Discord message limit
