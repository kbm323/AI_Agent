"""Tests for the Qwen router prompt template module.

These tests verify the prompt structure, field coverage, topic injection,
and determinism WITHOUT making any LLM calls.  Response parsing is also
tested with static fixtures.
"""

import json
import textwrap

import pytest

from src.qwen_router import (
    ALL_ROLES,
    AGENDA_TYPES,
    REQUIRED_OUTPUT_FIELDS,
    REQUIRED_PROMPT_SECTIONS,
    RISK_CATEGORIES,
    TEAM_ROLES,
    QwenClassificationResult,
    build_classification_prompt,
    parse_classification_response,
)


# ── Constants for tests ────────────────────────────────────────────────

_SAMPLE_TOPIC = (
    "신규 캐릭터 '루나'의 비주얼 디자인을 논의하고, SNS 홍보 전략을 수립하며, "
    "기존 게임엔진 백엔드 API 리팩토링도 함께 검토해주세요."
)

_SIMPLE_TOPIC = "뮤직비디오 오프닝 아이디어 회의"


# ── Prompt structure tests ─────────────────────────────────────────────

class TestPromptStructure:
    """Verify the classification prompt contains all required sections."""

    def test_all_required_sections_present(self):
        """Every REQUIRED_PROMPT_SECTIONS heading is in the prompt."""
        prompt = build_classification_prompt(_SAMPLE_TOPIC)
        for section in REQUIRED_PROMPT_SECTIONS:
            assert section in prompt, (
                f"Section '{section}' missing from prompt"
            )

    def test_sections_appear_in_order(self):
        """Sections must appear in the defined order."""
        prompt = build_classification_prompt(_SAMPLE_TOPIC)
        positions = {s: prompt.index(s) for s in REQUIRED_PROMPT_SECTIONS}
        for i in range(len(REQUIRED_PROMPT_SECTIONS) - 1):
            assert positions[REQUIRED_PROMPT_SECTIONS[i]] < positions[
                REQUIRED_PROMPT_SECTIONS[i + 1]
            ], (
                f"Section '{REQUIRED_PROMPT_SECTIONS[i]}' must appear before "
                f"'{REQUIRED_PROMPT_SECTIONS[i + 1]}'"
            )


# ── Field coverage tests ───────────────────────────────────────────────

class TestFieldCoverage:
    """Verify every required output field is requested in the prompt."""

    def test_agenda_type_requested(self):
        prompt = build_classification_prompt(_SAMPLE_TOPIC)
        assert '"agenda_type"' in prompt

    def test_tags_requested(self):
        prompt = build_classification_prompt(_SAMPLE_TOPIC)
        assert '"tags"' in prompt

    def test_risk_tags_requested(self):
        prompt = build_classification_prompt(_SAMPLE_TOPIC)
        assert '"risk_tags"' in prompt

    def test_required_roles_requested(self):
        prompt = build_classification_prompt(_SAMPLE_TOPIC)
        assert '"required_roles"' in prompt

    def test_optional_roles_requested(self):
        prompt = build_classification_prompt(_SAMPLE_TOPIC)
        assert '"optional_roles"' in prompt

    def test_validator_required_requested(self):
        prompt = build_classification_prompt(_SAMPLE_TOPIC)
        assert '"validator_required"' in prompt

    def test_codex_required_requested(self):
        prompt = build_classification_prompt(_SAMPLE_TOPIC)
        assert '"codex_required"' in prompt

    def test_all_output_fields_in_prompt(self):
        """All REQUIRED_OUTPUT_FIELDS appear as JSON keys in the template."""
        prompt = build_classification_prompt(_SAMPLE_TOPIC)
        for field in REQUIRED_OUTPUT_FIELDS:
            assert f'"{field}"' in prompt, (
                f"Output field '{field}' not found in prompt template"
            )

    def test_json_output_format_section_present(self):
        """Prompt must instruct Qwen to return JSON only."""
        prompt = build_classification_prompt(_SAMPLE_TOPIC)
        assert "json" in prompt.lower(), "Prompt must mention JSON output"
        assert "Output Format" in prompt


# ── Topic injection tests ──────────────────────────────────────────────

class TestTopicInjection:
    """Verify the meeting topic is correctly injected into the prompt."""

    def test_topic_appears_in_prompt(self):
        prompt = build_classification_prompt(_SAMPLE_TOPIC)
        # The topic appears in the "Meeting Topic" section at the end
        topic_section_start = prompt.index("## Meeting Topic")
        after_topic = prompt[topic_section_start:]
        assert _SAMPLE_TOPIC in after_topic

    def test_topic_is_last_section(self):
        """Topic should be the last major section so Qwen sees it at end."""
        prompt = build_classification_prompt(_SAMPLE_TOPIC)
        topic_idx = prompt.index("## Meeting Topic")
        # No other ## sections after meeting topic
        remaining = prompt[topic_idx + len("## Meeting Topic"):]
        assert "## " not in remaining, (
            "No other ##-level sections should follow Meeting Topic"
        )

    def test_different_topics_produce_different_prompts(self):
        prompt_a = build_classification_prompt(_SAMPLE_TOPIC)
        prompt_b = build_classification_prompt(_SIMPLE_TOPIC)
        assert prompt_a != prompt_b

    def test_same_topic_produces_identical_prompt(self):
        """Determinism: same topic → byte-identical prompt."""
        prompt_1 = build_classification_prompt(_SAMPLE_TOPIC)
        prompt_2 = build_classification_prompt(_SAMPLE_TOPIC)
        assert prompt_1 == prompt_2

    def test_topic_with_whitespace_trimmed(self):
        """Leading/trailing whitespace in topic is stripped."""
        prompt_raw = build_classification_prompt(f"  {_SIMPLE_TOPIC}  ")
        prompt_clean = build_classification_prompt(_SIMPLE_TOPIC)
        assert prompt_raw == prompt_clean

    def test_empty_topic_raises_value_error(self):
        with pytest.raises(ValueError, match="must not be empty"):
            build_classification_prompt("")

    def test_whitespace_only_topic_raises_value_error(self):
        with pytest.raises(ValueError, match="must not be empty"):
            build_classification_prompt("   \n\t  ")


# ── Team and role registry tests ───────────────────────────────────────

class TestTeamRoleRegistry:
    """Verify the embedded team/role registry is complete and consistent."""

    def test_six_teams_registered(self):
        assert len(TEAM_ROLES) == 6, (
            f"Expected 6 teams, got {len(TEAM_ROLES)}: "
            f"{list(TEAM_ROLES.keys())}"
        )

    def test_total_role_count(self):
        """Should have 29 specialised roles (counting coordinator)."""
        assert len(ALL_ROLES) == 29, (
            f"Expected 29 total roles, got {len(ALL_ROLES)}"
        )

    def test_every_team_has_leader(self):
        """Every team except coordination should have at least one leader."""
        for team_name, roles in TEAM_ROLES.items():
            if team_name == "coordination":
                continue  # coordinator is its own role_type
            leader_count = sum(
                1 for r in roles if r["role_type"] == "leader"
            )
            assert leader_count >= 1, (
                f"Team '{team_name}' has no leader role"
            )

    def test_coordinator_present(self):
        coordinator_ids = [
            r["role_id"] for r in ALL_ROLES if r["role_id"] == "coordinator"
        ]
        assert len(coordinator_ids) == 1

    def test_all_roles_have_required_fields(self):
        required_role_fields = {"role_id", "display_name", "role_type",
                                "description"}
        for role in ALL_ROLES:
            missing = required_role_fields - set(role.keys())
            assert not missing, (
                f"Role {role.get('role_id', '???')} missing fields: {missing}"
            )

    def test_no_duplicate_role_ids(self):
        role_ids = [r["role_id"] for r in ALL_ROLES]
        assert len(role_ids) == len(set(role_ids)), (
            f"Duplicate role IDs found: "
            f"{sorted(set(r for r in role_ids if role_ids.count(r) > 1))}"
        )

    def test_teams_appear_in_prompt(self):
        """Every team name format appears somewhere in the prompt."""
        prompt = build_classification_prompt(_SAMPLE_TOPIC)
        team_display_names = {
            "Coordination", "Content Production", "Art Design",
            "Tech Development", "Marketing", "Execution",
        }
        for name in team_display_names:
            assert name in prompt, f"Team '{name}' not found in prompt"


# ── Agenda type coverage tests ─────────────────────────────────────────

class TestAgendaTypes:
    """Verify all agenda types are defined and referenced in the prompt."""

    def test_all_agenda_types_in_prompt(self):
        prompt = build_classification_prompt(_SIMPLE_TOPIC)
        for at in AGENDA_TYPES:
            assert at["type"] in prompt, (
                f"Agenda type '{at['type']}' not in prompt"
            )

    def test_agenda_types_unique(self):
        types = [at["type"] for at in AGENDA_TYPES]
        assert len(types) == len(set(types)), (
            f"Duplicate agenda types: {types}"
        )


# ── Risk category coverage tests ───────────────────────────────────────

class TestRiskCategories:
    """Verify all risk categories are defined and referenced."""

    def test_all_risk_tags_in_prompt(self):
        prompt = build_classification_prompt(_SIMPLE_TOPIC)
        for rc in RISK_CATEGORIES:
            assert rc["tag"] in prompt, (
                f"Risk tag '{rc['tag']}' not in prompt"
            )

    def test_risk_tags_unique(self):
        tags = [rc["tag"] for rc in RISK_CATEGORIES]
        assert len(tags) == len(set(tags)), f"Duplicate risk tags: {tags}"


# ── Token / size budget tests ──────────────────────────────────────────

class TestPromptSize:
    """Verify the prompt stays within reasonable size limits."""

    def test_prompt_under_8000_chars(self):
        """Prompt should fit within ~2k tokens (≈8k chars at 4 chars/token)."""
        prompt = build_classification_prompt(_SAMPLE_TOPIC)
        assert len(prompt) < 8000, (
            f"Prompt is {len(prompt)} chars — exceeds 8000 budget"
        )

    def test_prompt_size_stable_across_topics(self):
        """Prompt size should not vary wildly with different topics."""
        sizes = []
        for topic in (_SIMPLE_TOPIC, _SAMPLE_TOPIC, "긴급: 서버 다운",
                      "보안 취약점 발견됨"):
            sizes.append(len(build_classification_prompt(topic)))
        # All sizes within ±500 chars of each other
        size_range = max(sizes) - min(sizes)
        assert size_range < 500, (
            f"Prompt size varies by {size_range} chars across topics"
        )


# ── Response parsing tests ─────────────────────────────────────────────

class TestParseClassificationResponse:
    """Verify classification response JSON parsing."""

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

    def test_parses_clean_json(self):
        result = parse_classification_response(self._VALID_JSON)
        assert result.agenda_type == "creative_production"
        assert result.tags == (
            "character-design", "visual-concept", "sns-strategy",
        )
        assert result.risk_tags == ("brand",)
        assert result.required_roles == (
            "coordinator", "art-director", "marketing-lead",
        )
        assert result.optional_roles == ("concept-artist", "sns-strategist")
        assert result.validator_required is True
        assert result.codex_required is False
        assert result.confidence == 0.92
        assert "Visual design" in result.reasoning

    def test_parses_markdown_fenced_json(self):
        raw = textwrap.dedent("""\
            Here is the classification:
            ```json
            {
              "agenda_type": "technical_development",
              "tags": ["backend-api", "refactoring"],
              "risk_tags": [],
              "required_roles": ["coordinator", "tech-director"],
              "optional_roles": [],
              "validator_required": false,
              "codex_required": false,
              "confidence": 0.85,
              "reasoning": "Pure technical refactoring, low risk."
            }
            ```
            Let me know if you need anything else.
        """)
        result = parse_classification_response(raw)
        assert result.agenda_type == "technical_development"
        assert result.validator_required is False
        assert result.codex_required is False

    def test_parses_json_with_leading_trailing_text(self):
        raw = (
            "Some preamble text\n"
            + self._VALID_JSON
            + "\nSome trailing text"
        )
        result = parse_classification_response(raw)
        assert result.agenda_type == "creative_production"

    def test_missing_fields_get_defaults(self):
        raw = json.dumps({"agenda_type": "general_planning"})
        result = parse_classification_response(raw)
        assert result.agenda_type == "general_planning"
        assert result.tags == ()
        assert result.risk_tags == ()
        assert result.required_roles == ()
        assert result.optional_roles == ()
        assert result.validator_required is False
        assert result.codex_required is False
        assert result.confidence == 0.5
        assert result.reasoning == ""

    def test_invalid_json_raises_value_error(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            parse_classification_response("This is not JSON at all")

    def test_empty_response_raises_value_error(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            parse_classification_response("")

    def test_tags_normalised_to_lowercase(self):
        raw = json.dumps({
            "tags": ["Character-Design", "  SNS STRATEGY  "],
        })
        result = parse_classification_response(raw)
        assert result.tags == ("character-design", "sns strategy")

    def test_confidence_clamped_to_range(self):
        raw_low = json.dumps({"confidence": -1.5})
        raw_high = json.dumps({"confidence": 2.5})
        assert parse_classification_response(raw_low).confidence == 0.0
        assert parse_classification_response(raw_high).confidence == 1.0

    def test_result_is_frozen(self):
        result = parse_classification_response(self._VALID_JSON)
        with pytest.raises(Exception):
            result.agenda_type = "changed"  # type: ignore[misc]


# ── Classification rules coverage ──────────────────────────────────────

class TestClassificationRulesInPrompt:
    """Verify classification guidance is present in the prompt."""

    def test_rules_numbered_1_through_9(self):
        prompt = build_classification_prompt(_SAMPLE_TOPIC)
        for i in range(1, 10):
            assert f"{i}." in prompt, (
                f"Classification rule {i} not found in prompt"
            )

    def test_quorum_rule_mentions_coordinator(self):
        """Rule 4 must state coordinator is always required."""
        prompt = build_classification_prompt(_SAMPLE_TOPIC)
        rules_section_start = prompt.index("Classification Rules")
        rules_section_end = prompt.index("Output Format")
        rules = prompt[rules_section_start:rules_section_end]
        assert "coordinator" in rules.lower()

    def test_validator_triggers_described(self):
        prompt = build_classification_prompt(_SAMPLE_TOPIC)
        assert "budget" in prompt.lower()
        assert "legal" in prompt.lower()


# ── English content requirement ────────────────────────────────────────

class TestPromptLanguage:
    """Prompt must use English for reliable LLM classification."""

    def test_classification_rules_in_english(self):
        """Korean in system instructions would confuse opencode-go models."""
        prompt = build_classification_prompt(_SAMPLE_TOPIC)
        rules_start = prompt.index("Classification Rules")
        rules_end = prompt.index("Output Format")
        rules_text = prompt[rules_start:rules_end]
        # Check that Korean is limited to topic injection only
        # (system parts should be in English)
        system_text = prompt[:prompt.index("## Meeting Topic")]
        # Count Korean characters
        korean_count = sum(
            1 for c in system_text if "\uac00" <= c <= "\ud7af"
        )
        assert korean_count == 0, (
            f"System prompt contains {korean_count} Korean characters — "
            f"must be English-only for reliable LLM classification"
        )
