"""Tests for the per-persona single opinion generator.

Sub-AC 5a-2: Per-persona single opinion generation — given one persona
definition and one agenda item, produce a complete opinion packet via
LLM call, testable by mocking the LLM and verifying output matches
the expected packet schema.

All LLM calls are mocked via injectable ``SubprocessRunner`` — no real
``opencode-go`` binary or network access required.

Coverage:
- Prompt building (deterministic, includes persona/agenda fields)
- Command construction (model, context-file, ordering)
- Response parsing (clean JSON, markdown-fenced, invalid, empty)
- Full integration via mock runner: persona + agenda → valid opinion packet
- Schema validation (LLM output conforms to opinion packet schema)
- Error scenarios: LLM non-zero exit, timeout, bad JSON, missing fields,
  wrong types, out-of-range confidence
- PersonaDefinition validation and immutability
- Runner injection and restoration
- Duration tracking
- Multiple persona types (leader, worker, different teams/models)
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from src.opinion_packet_validator import (
    OpinionFieldValidationError,
    OpinionPacketValidationReport,
    validate_opinion_packet,
)
from src.persona_opinion_generator import (
    OpinionGenerationResult,
    PersonaDefinition,
    SubprocessRunner,
    _default_subprocess_runner,
    build_opencode_command,
    build_opinion_prompt,
    generate_opinion,
    get_runner,
    inject_runner,
    parse_opinion_response,
)


# ═════════════════════════════════════════════════════════════════════════
# Fixtures
# ═════════════════════════════════════════════════════════════════════════


@pytest.fixture
def art_director_persona() -> PersonaDefinition:
    """Standard Art Director persona from routing_rules.yaml."""
    return PersonaDefinition(
        role_id="art-director",
        display_name="아트 디렉터",
        team="art-design",
        role_type="leader",
        expertise_tags=(
            "visual_direction",
            "art_style",
            "design_system",
            "character_design",
            "vfx_direction",
            "ui_ux_oversight",
        ),
        model_name="qwen-max",
        model_fallback="deepseek-v3",
        persona_description=(
            "You are the Art Director of an AI entertainment company. "
            "Your visual identity decisions shape every creative output."
        ),
    )


@pytest.fixture
def tech_director_persona() -> PersonaDefinition:
    """Standard Tech Director persona."""
    return PersonaDefinition(
        role_id="tech-director",
        display_name="테크 디렉터",
        team="tech-engineering",
        role_type="leader",
        expertise_tags=(
            "technical_architecture",
            "system_design",
            "code_review",
            "infrastructure",
            "security_oversight",
        ),
        model_name="deepseek-v3",
        model_fallback="qwen-max",
    )


@pytest.fixture
def marketing_worker_persona() -> PersonaDefinition:
    """Marketing worker persona (SNS Strategist)."""
    return PersonaDefinition(
        role_id="sns-strategist",
        display_name="SNS 전략가",
        team="marketing",
        role_type="worker",
        expertise_tags=("sns_strategy", "content_planning", "audience_growth"),
        model_name="qwen-max",
        model_fallback="deepseek-v3",
    )


@pytest.fixture
def valid_opinion_json() -> str:
    """A fully valid opinion packet JSON response from the LLM."""
    return json.dumps(
        {
            "persona_id": "art-director",
            "agenda_item_ref": "character-visual-concept",
            "opinion_content": (
                "I recommend a neon-noir visual palette with high-contrast "
                "silhouettes for the protagonist line. The color scheme should "
                "draw from cyberpunk aesthetics with purple-cyan gradients. "
                "For supporting characters, a more muted palette will create "
                "visual hierarchy. Risks: this style may not appeal to the "
                "casual mobile gaming audience, so we should validate with "
                "market research before finalizing."
            ),
            "confidence": 0.88,
            "timestamp": "2026-06-10T14:30:00Z",
        },
        ensure_ascii=False,
    )


@pytest.fixture
def opinion_json_with_fence(valid_opinion_json: str) -> str:
    """LLM response wrapped in markdown code fence (common artefact)."""
    return (
        f"```json\n{valid_opinion_json}\n```\n\n"
        "Let me know if you need any adjustments."
    )


@pytest.fixture
def opinion_json_with_preamble(valid_opinion_json: str) -> str:
    """LLM response with preamble text before the JSON."""
    return (
        "Here is my opinion on the character visual concept:\n\n"
        f"{valid_opinion_json}\n\n"
        "I hope this helps the discussion."
    )


# ═════════════════════════════════════════════════════════════════════════
# Mock subprocess runner helpers
# ═════════════════════════════════════════════════════════════════════════


def _make_mock_runner(
    exit_code: int,
    stdout: str,
    stderr: str = "",
) -> SubprocessRunner:
    """Return a mock runner that returns fixed values and records calls."""

    calls: list[dict[str, Any]] = []

    def _runner(
        command: list[str],
        timeout_seconds: float,
        env: dict[str, str] | None,
        workdir: str | None,
    ) -> tuple[int, str, str]:
        calls.append(
            {
                "command": command,
                "timeout_seconds": timeout_seconds,
                "env": env,
                "workdir": workdir,
            }
        )
        return (exit_code, stdout, stderr)

    _runner.calls = calls  # type: ignore[attr-defined]
    return _runner


def _make_tracking_runner(
    stdout: str = "{}",
) -> SubprocessRunner:
    """Return a mock runner that succeeds and tracks arguments."""
    calls: list[dict[str, Any]] = []

    def _runner(
        command: list[str],
        timeout_seconds: float,
        env: dict[str, str] | None,
        workdir: str | None,
    ) -> tuple[int, str, str]:
        calls.append(
            {
                "command": command,
                "timeout_seconds": timeout_seconds,
                "env": env,
                "workdir": workdir,
            }
        )
        return (0, stdout, "")

    _runner.calls = calls  # type: ignore[attr-defined]
    return _runner


# ═════════════════════════════════════════════════════════════════════════
# 1. PersonaDefinition tests
# ═════════════════════════════════════════════════════════════════════════


class TestPersonaDefinition:
    """Verify PersonaDefinition construction, validation, and immutability."""

    def test_minimal_persona_construction(self) -> None:
        """A minimal persona with only required fields should construct."""
        p = PersonaDefinition(
            role_id="test-role",
            display_name="Test Role",
            team="test-team",
            expertise_tags=("testing",),
        )
        assert p.role_id == "test-role"
        assert p.model_provider == "opencode-go"  # default
        assert p.model_name == "qwen-max"  # default
        assert p.model_fallback == "deepseek-v3"  # default
        assert p.role_type == "worker"  # default

    def test_full_persona_construction(
        self, art_director_persona: PersonaDefinition
    ) -> None:
        """A full persona with all fields should construct without errors."""
        p = art_director_persona
        assert p.role_id == "art-director"
        assert p.display_name == "아트 디렉터"
        assert p.team == "art-design"
        assert p.role_type == "leader"
        assert "visual_direction" in p.expertise_tags
        assert len(p.expertise_tags) == 6
        assert p.model_name == "qwen-max"
        assert p.model_fallback == "deepseek-v3"
        assert len(p.persona_description) > 0

    def test_empty_role_id_raises_value_error(self) -> None:
        """Empty role_id must raise ValueError."""
        with pytest.raises(ValueError, match="role_id must be"):
            PersonaDefinition(
                role_id="",
                display_name="Test",
                team="test",
                expertise_tags=(),
            )

    def test_whitespace_role_id_raises_value_error(self) -> None:
        """Whitespace-only role_id must raise ValueError."""
        with pytest.raises(ValueError, match="role_id must be"):
            PersonaDefinition(
                role_id="   ",
                display_name="Test",
                team="test",
                expertise_tags=(),
            )

    def test_empty_display_name_raises_value_error(self) -> None:
        """Empty display_name must raise ValueError."""
        with pytest.raises(ValueError, match="display_name must be"):
            PersonaDefinition(
                role_id="test",
                display_name="",
                team="test",
                expertise_tags=(),
            )

    def test_empty_model_name_raises_value_error(self) -> None:
        """Empty model_name must raise ValueError."""
        with pytest.raises(ValueError, match="model_name must be"):
            PersonaDefinition(
                role_id="test",
                display_name="Test",
                team="test",
                expertise_tags=(),
                model_name="",
            )

    def test_persona_is_frozen(self) -> None:
        """PersonaDefinition must be immutable."""
        p = PersonaDefinition(
            role_id="test",
            display_name="Test",
            team="test",
            expertise_tags=(),
        )
        with pytest.raises(Exception):
            p.role_id = "changed"  # type: ignore[misc]

    def test_to_dict(self, art_director_persona: PersonaDefinition) -> None:
        """to_dict() should produce a serializable dict with correct keys."""
        d = art_director_persona.to_dict()
        assert d["role_id"] == "art-director"
        assert d["display_name"] == "아트 디렉터"
        assert d["team"] == "art-design"
        assert isinstance(d["expertise_tags"], list)
        assert d["model_name"] == "qwen-max"

    def test_empty_expertise_tags_allowed(self) -> None:
        """A persona with empty expertise_tags should be valid."""
        p = PersonaDefinition(
            role_id="generic",
            display_name="Generic Role",
            team="general",
            expertise_tags=(),
        )
        assert len(p.expertise_tags) == 0

    def test_different_model_providers(self) -> None:
        """Custom model_provider should be stored correctly."""
        p = PersonaDefinition(
            role_id="test",
            display_name="Test",
            team="test",
            expertise_tags=(),
            model_provider="custom-provider",
        )
        assert p.model_provider == "custom-provider"


# ═════════════════════════════════════════════════════════════════════════
# 2. Prompt building tests
# ═════════════════════════════════════════════════════════════════════════


class TestBuildOpinionPrompt:
    """Verify prompt construction is deterministic and persona-aware."""

    def test_basic_prompt_structure(
        self, art_director_persona: PersonaDefinition
    ) -> None:
        """Prompt must contain persona identity fields and agenda context."""
        prompt = build_opinion_prompt(
            art_director_persona,
            "character-visual-concept",
            "We need to decide the visual identity for the new character line.",
        )
        assert "art-director" in prompt
        assert "아트 디렉터" in prompt
        assert "art-design" in prompt
        assert "leader" in prompt
        assert "visual_direction" in prompt
        assert "character-visual-concept" in prompt
        assert "visual identity" in prompt

    def test_prompt_includes_expertise_tags(
        self, art_director_persona: PersonaDefinition
    ) -> None:
        """Expertise tags must be rendered in the prompt."""
        prompt = build_opinion_prompt(
            art_director_persona,
            "test-item",
            "test context",
        )
        for tag in ("visual_direction", "art_style", "character_design"):
            assert tag in prompt, f"Expertise tag '{tag}' missing from prompt"

    def test_prompt_includes_persona_description(
        self, art_director_persona: PersonaDefinition
    ) -> None:
        """Persona description must appear in the prompt when non-empty."""
        prompt = build_opinion_prompt(
            art_director_persona,
            "test-item",
            "test context",
        )
        assert "Persona Context" in prompt
        assert "Art Director of an AI entertainment company" in prompt

    def test_prompt_without_persona_description(
        self, tech_director_persona: PersonaDefinition
    ) -> None:
        """When persona_description is empty, no 'Persona Context' section."""
        prompt = build_opinion_prompt(
            tech_director_persona,
            "test-item",
            "test context",
        )
        assert "Persona Context" not in prompt

    def test_prompt_includes_output_schema(self) -> None:
        """Prompt must instruct LLM about the required JSON output schema."""
        p = PersonaDefinition(
            role_id="test",
            display_name="Test",
            team="test-team",
            expertise_tags=("testing",),
        )
        prompt = build_opinion_prompt(p, "ref", "ctx")
        assert "persona_id" in prompt
        assert "agenda_item_ref" in prompt
        assert "opinion_content" in prompt
        assert "confidence" in prompt
        assert "timestamp" in prompt
        assert "ISO-8601" in prompt

    def test_prompt_deterministic_same_inputs(
        self, art_director_persona: PersonaDefinition
    ) -> None:
        """Same inputs must produce byte-identical prompts."""
        p1 = build_opinion_prompt(art_director_persona, "ref", "ctx")
        p2 = build_opinion_prompt(art_director_persona, "ref", "ctx")
        assert p1 == p2

    def test_different_personas_produce_different_prompts(
        self,
        art_director_persona: PersonaDefinition,
        tech_director_persona: PersonaDefinition,
    ) -> None:
        """Different personas must produce different prompts."""
        p1 = build_opinion_prompt(art_director_persona, "ref", "ctx")
        p2 = build_opinion_prompt(tech_director_persona, "ref", "ctx")
        assert p1 != p2

    def test_different_agenda_items_produce_different_prompts(
        self, art_director_persona: PersonaDefinition
    ) -> None:
        """Different agenda items must produce different prompts."""
        p1 = build_opinion_prompt(art_director_persona, "ref-a", "ctx")
        p2 = build_opinion_prompt(art_director_persona, "ref-b", "ctx")
        assert p1 != p2

    def test_empty_agenda_item_ref_raises_value_error(
        self, art_director_persona: PersonaDefinition
    ) -> None:
        """Empty agenda_item_ref must raise ValueError."""
        with pytest.raises(ValueError, match="agenda_item_ref must be"):
            build_opinion_prompt(art_director_persona, "", "ctx")

    def test_whitespace_agenda_item_ref_raises_value_error(
        self, art_director_persona: PersonaDefinition
    ) -> None:
        """Whitespace-only agenda_item_ref must raise ValueError."""
        with pytest.raises(ValueError, match="agenda_item_ref must be"):
            build_opinion_prompt(art_director_persona, "   ", "ctx")

    def test_empty_agenda_context_raises_value_error(
        self, art_director_persona: PersonaDefinition
    ) -> None:
        """Empty agenda_context must raise ValueError."""
        with pytest.raises(ValueError, match="agenda_context must be"):
            build_opinion_prompt(art_director_persona, "ref", "")

    def test_empty_expertise_tags_renders_general(
        self,
    ) -> None:
        """Empty expertise_tags should render as 'general'."""
        p = PersonaDefinition(
            role_id="test",
            display_name="Test",
            team="test",
            expertise_tags=(),
        )
        prompt = build_opinion_prompt(p, "ref", "ctx")
        assert "general" in prompt

    def test_prompt_length_is_reasonable(
        self, art_director_persona: PersonaDefinition
    ) -> None:
        """Prompt should be under token budget (~3k chars for this AC)."""
        prompt = build_opinion_prompt(
            art_director_persona,
            "character-visual-concept",
            "Long context: " + "X" * 500,
        )
        assert len(prompt) < 3000  # well under 12k token budget


# ═════════════════════════════════════════════════════════════════════════
# 3. Command construction tests
# ═════════════════════════════════════════════════════════════════════════


class TestBuildOpencodeCommand:
    """Verify command list construction for opinion generation calls."""

    def test_basic_command_structure(self) -> None:
        """Command must start with 'opencode-go' and include flags."""
        cmd = build_opencode_command("qwen-max", "/tmp/packet.json")
        assert cmd[0] == "opencode-go"
        assert "--model" in cmd
        assert "--context-file" in cmd
        assert cmd == [
            "opencode-go",
            "--model",
            "qwen-max",
            "--context-file",
            "/tmp/packet.json",
        ]

    def test_command_length_is_exactly_5(self) -> None:
        """Command should have exactly 5 elements."""
        cmd = build_opencode_command("qwen-max", "/tmp/p.json")
        assert len(cmd) == 5

    def test_no_prompt_flag(self) -> None:
        """--prompt flag is FORBIDDEN per Track 5 design."""
        cmd = build_opencode_command("qwen-max", "/tmp/p.json")
        assert "--prompt" not in cmd

    def test_different_models(self) -> None:
        """Different models must produce different commands."""
        cmd_a = build_opencode_command("qwen-max", "/tmp/a.json")
        cmd_b = build_opencode_command("glm-5.1", "/tmp/a.json")
        assert cmd_a != cmd_b

    def test_empty_model_raises(self) -> None:
        """Empty model must raise ValueError."""
        with pytest.raises(ValueError, match="model must be"):
            build_opencode_command("", "/tmp/p.json")

    def test_empty_context_file_raises(self) -> None:
        """Empty context_file must raise ValueError."""
        with pytest.raises(ValueError, match="context_file must be"):
            build_opencode_command("qwen-max", "")

    def test_whitespace_trimmed(self) -> None:
        """Leading/trailing whitespace should be stripped."""
        cmd = build_opencode_command("  deepseek-v3  ", "  /tmp/p.json  ")
        assert cmd[2] == "deepseek-v3"
        assert cmd[4] == "/tmp/p.json"


# ═════════════════════════════════════════════════════════════════════════
# 4. Response parsing tests
# ═════════════════════════════════════════════════════════════════════════


class TestParseOpinionResponse:
    """Verify parse_opinion_response extracts JSON from LLM output."""

    def test_clean_json(self, valid_opinion_json: str) -> None:
        """Direct JSON string should parse correctly."""
        result = parse_opinion_response(valid_opinion_json)
        assert result["persona_id"] == "art-director"
        assert result["agenda_item_ref"] == "character-visual-concept"
        assert result["confidence"] == 0.88
        assert result["timestamp"] == "2026-06-10T14:30:00Z"

    def test_markdown_fence(self, opinion_json_with_fence: str) -> None:
        """JSON inside ```json fence should be extracted."""
        result = parse_opinion_response(opinion_json_with_fence)
        assert result["persona_id"] == "art-director"

    def test_backtick_fence_without_json_tag(
        self, valid_opinion_json: str
    ) -> None:
        """Plain ``` fence (no json tag) should also work."""
        text = f"```\n{valid_opinion_json}\n```"
        result = parse_opinion_response(text)
        assert result["persona_id"] == "art-director"

    def test_preamble_text(self, opinion_json_with_preamble: str) -> None:
        """Text before JSON should be ignored."""
        result = parse_opinion_response(opinion_json_with_preamble)
        assert result["persona_id"] == "art-director"

    def test_trailing_text(self, valid_opinion_json: str) -> None:
        """Text after JSON should be ignored."""
        text = f"{valid_opinion_json}\n\nLet me know if you need more detail."
        result = parse_opinion_response(text)
        assert result["persona_id"] == "art-director"

    def test_nested_json_object(self) -> None:
        """JSON with nested objects should be parsed."""
        text = json.dumps(
            {
                "persona_id": "tech-director",
                "agenda_item_ref": "infra-design",
                "opinion_content": 'We should use {"kubernetes": true} for orchestration.',
                "confidence": 0.95,
                "timestamp": "2026-06-10T14:30:00Z",
            }
        )
        result = parse_opinion_response(text)
        assert result["persona_id"] == "tech-director"
        assert "kubernetes" in result["opinion_content"]

    def test_empty_string_raises(self) -> None:
        """Empty string must raise ValueError."""
        with pytest.raises(ValueError, match="No JSON object found"):
            parse_opinion_response("")

    def test_no_braces_raises(self) -> None:
        """Text without JSON object must raise ValueError."""
        with pytest.raises(ValueError, match="No JSON object found"):
            parse_opinion_response("This is just plain text, no JSON here.")

    def test_malformed_json_raises(self) -> None:
        """Malformed JSON must raise ValueError."""
        with pytest.raises(ValueError, match="Invalid JSON"):
            parse_opinion_response('{"persona_id": "test", "broken: true}')

    def test_json_array_extracts_inner_object(self) -> None:
        """JSON array wrapping an object should extract the inner object."""
        result = parse_opinion_response('[{"persona_id": "test", "agenda_item_ref": "a", "opinion_content": "ok", "confidence": 0.5, "timestamp": "2026-06-10T00:00:00Z"}]')
        assert result["persona_id"] == "test"

    def test_bare_json_array_without_object_raises(self) -> None:
        """JSON array without any object must raise ValueError."""
        with pytest.raises(ValueError, match="No JSON object found"):
            parse_opinion_response('[1, 2, 3]')

    def test_unicode_content(self) -> None:
        """Korean/Unicode in opinion_content must be preserved."""
        text = json.dumps(
            {
                "persona_id": "art-director",
                "agenda_item_ref": "한글-테스트",
                "opinion_content": "한글 의견 내용입니다. 🎨 캐릭터 디자인 추천.",
                "confidence": 0.92,
                "timestamp": "2026-06-10T14:30:00Z",
            },
            ensure_ascii=False,
        )
        result = parse_opinion_response(text)
        assert "한글" in result["opinion_content"]
        assert "🎨" in result["opinion_content"]


# ═════════════════════════════════════════════════════════════════════════
# 5. Full integration tests (mock LLM runner)
# ═════════════════════════════════════════════════════════════════════════


class TestGenerateOpinion:
    """Verify the complete generate_opinion pipeline with mock LLM."""

    # ── Success cases ──

    def test_successful_opinion_generation(
        self,
        art_director_persona: PersonaDefinition,
        valid_opinion_json: str,
    ) -> None:
        """A valid LLM response should produce a valid opinion packet."""
        runner = _make_mock_runner(0, valid_opinion_json)
        result = generate_opinion(
            persona=art_director_persona,
            agenda_item_ref="character-visual-concept",
            agenda_context="캐릭터 비주얼 컨셉 회의",
            _injected_runner=runner,
        )

        assert result.success is True
        assert result.opinion_packet is not None
        assert result.opinion_packet["persona_id"] == "art-director"  # type: ignore[index]
        assert result.role_id == "art-director"
        assert result.model_name == "qwen-max"
        assert result.duration_seconds >= 0.0
        assert result.error_message == ""
        assert result.validation_report is not None
        assert result.validation_report.passed is True

    def test_result_opinion_packet_passes_schema_validation(
        self,
        art_director_persona: PersonaDefinition,
        valid_opinion_json: str,
    ) -> None:
        """The returned opinion packet must pass the schema validator."""
        runner = _make_mock_runner(0, valid_opinion_json)
        result = generate_opinion(
            persona=art_director_persona,
            agenda_item_ref="character-visual-concept",
            agenda_context="test context",
            _injected_runner=runner,
        )

        assert result.success is True
        # Run validator independently to double-check
        report = validate_opinion_packet(result.opinion_packet)
        assert report.passed is True

    def test_markdown_fenced_response_succeeds(
        self,
        art_director_persona: PersonaDefinition,
        opinion_json_with_fence: str,
    ) -> None:
        """LLM response with markdown fence should still succeed."""
        runner = _make_mock_runner(0, opinion_json_with_fence)
        result = generate_opinion(
            persona=art_director_persona,
            agenda_item_ref="test-item",
            agenda_context="test context",
            _injected_runner=runner,
        )

        assert result.success is True
        assert result.opinion_packet is not None

    def test_preamble_response_succeeds(
        self,
        art_director_persona: PersonaDefinition,
        opinion_json_with_preamble: str,
    ) -> None:
        """LLM response with preamble text should still succeed."""
        runner = _make_mock_runner(0, opinion_json_with_preamble)
        result = generate_opinion(
            persona=art_director_persona,
            agenda_item_ref="test-item",
            agenda_context="test context",
            _injected_runner=runner,
        )

        assert result.success is True

    def test_different_models_used(
        self,
        tech_director_persona: PersonaDefinition,
    ) -> None:
        """Tech director should use deepseek-v3 model."""
        valid_json = json.dumps(
            {
                "persona_id": "tech-director",
                "agenda_item_ref": "architecture-review",
                "opinion_content": "We should adopt a microservices architecture.",
                "confidence": 0.90,
                "timestamp": "2026-06-10T14:30:00Z",
            }
        )
        runner = _make_mock_runner(0, valid_json)

        result = generate_opinion(
            persona=tech_director_persona,
            agenda_item_ref="architecture-review",
            agenda_context="Review system architecture",
            _injected_runner=runner,
        )

        assert result.success is True
        assert result.model_name == "deepseek-v3"
        # Verify command used correct model
        assert len(runner.calls) == 1  # type: ignore[attr-defined]
        cmd = runner.calls[0]["command"]  # type: ignore[attr-defined]
        assert "deepseek-v3" in cmd

    def test_worker_persona_generates_opinion(
        self,
        marketing_worker_persona: PersonaDefinition,
    ) -> None:
        """Worker-type persona should also generate valid opinions."""
        valid_json = json.dumps(
            {
                "persona_id": "sns-strategist",
                "agenda_item_ref": "sns-campaign-plan",
                "opinion_content": (
                    "We should launch on TikTok first with short-form content."
                ),
                "confidence": 0.85,
                "timestamp": "2026-06-10T14:30:00Z",
            }
        )
        runner = _make_mock_runner(0, valid_json)

        result = generate_opinion(
            persona=marketing_worker_persona,
            agenda_item_ref="sns-campaign-plan",
            agenda_context="SNS 캠페인 기획 회의",
            _injected_runner=runner,
        )

        assert result.success is True
        assert result.role_id == "sns-strategist"

    def test_long_opinion_content_accepted(
        self, art_director_persona: PersonaDefinition
    ) -> None:
        """Long opinion_content within token budget should succeed."""
        long_json = json.dumps(
            {
                "persona_id": "art-director",
                "agenda_item_ref": "long-analysis",
                "opinion_content": "X" * 4000,  # ~4k chars
                "confidence": 0.75,
                "timestamp": "2026-06-10T14:30:00Z",
            }
        )
        runner = _make_mock_runner(0, long_json)
        result = generate_opinion(
            persona=art_director_persona,
            agenda_item_ref="long-analysis",
            agenda_context="Detailed analysis needed",
            _injected_runner=runner,
        )
        assert result.success is True

    def test_duration_tracked(
        self,
        art_director_persona: PersonaDefinition,
        valid_opinion_json: str,
    ) -> None:
        """Duration should be a non-negative float."""
        runner = _make_mock_runner(0, valid_opinion_json)
        result = generate_opinion(
            persona=art_director_persona,
            agenda_item_ref="test",
            agenda_context="test",
            _injected_runner=runner,
        )
        assert isinstance(result.duration_seconds, float)
        assert result.duration_seconds >= 0.0

    def test_raw_llm_output_preserved_on_success(
        self,
        art_director_persona: PersonaDefinition,
        valid_opinion_json: str,
    ) -> None:
        """raw_llm_output should contain the full stdout."""
        runner = _make_mock_runner(0, valid_opinion_json)
        result = generate_opinion(
            persona=art_director_persona,
            agenda_item_ref="test",
            agenda_context="test",
            _injected_runner=runner,
        )
        assert result.raw_llm_output == valid_opinion_json

    # ── Schema validation edge cases ──

    def test_missing_field_causes_validation_failure(
        self, art_director_persona: PersonaDefinition
    ) -> None:
        """LLM response missing a required field should fail validation."""
        incomplete_json = json.dumps(
            {
                "persona_id": "art-director",
                "agenda_item_ref": "test",
                "opinion_content": "Some content",
                # missing confidence and timestamp
            }
        )
        runner = _make_mock_runner(0, incomplete_json)
        result = generate_opinion(
            persona=art_director_persona,
            agenda_item_ref="test",
            agenda_context="test",
            _injected_runner=runner,
        )

        assert result.success is False
        assert "Schema validation failed" in result.error_message
        assert result.validation_report is not None
        assert result.validation_report.passed is False

    def test_confidence_out_of_range_causes_failure(
        self, art_director_persona: PersonaDefinition
    ) -> None:
        """Confidence > 1.0 should fail schema validation."""
        bad_json = json.dumps(
            {
                "persona_id": "art-director",
                "agenda_item_ref": "test",
                "opinion_content": "Content",
                "confidence": 1.5,
                "timestamp": "2026-06-10T14:30:00Z",
            }
        )
        runner = _make_mock_runner(0, bad_json)
        result = generate_opinion(
            persona=art_director_persona,
            agenda_item_ref="test",
            agenda_context="test",
            _injected_runner=runner,
        )
        assert result.success is False
        assert "Schema validation failed" in result.error_message

    def test_invalid_timestamp_causes_failure(
        self, art_director_persona: PersonaDefinition
    ) -> None:
        """Invalid timestamp format should fail schema validation."""
        bad_json = json.dumps(
            {
                "persona_id": "art-director",
                "agenda_item_ref": "test",
                "opinion_content": "Content",
                "confidence": 0.5,
                "timestamp": "not-a-timestamp",
            }
        )
        runner = _make_mock_runner(0, bad_json)
        result = generate_opinion(
            persona=art_director_persona,
            agenda_item_ref="test",
            agenda_context="test",
            _injected_runner=runner,
        )
        assert result.success is False

    def test_invalid_persona_id_format_causes_failure(
        self, art_director_persona: PersonaDefinition
    ) -> None:
        """Non-kebab-case persona_id should fail schema validation."""
        bad_json = json.dumps(
            {
                "persona_id": "Art_Director",  # underscore + uppercase
                "agenda_item_ref": "test",
                "opinion_content": "Content",
                "confidence": 0.5,
                "timestamp": "2026-06-10T14:30:00Z",
            }
        )
        runner = _make_mock_runner(0, bad_json)
        result = generate_opinion(
            persona=art_director_persona,
            agenda_item_ref="test",
            agenda_context="test",
            _injected_runner=runner,
        )
        assert result.success is False

    # ── LLM failure cases ──

    def test_llm_non_zero_exit_causes_failure(
        self, art_director_persona: PersonaDefinition
    ) -> None:
        """Non-zero exit code should produce success=False."""
        runner = _make_mock_runner(1, "", "Model not found")
        result = generate_opinion(
            persona=art_director_persona,
            agenda_item_ref="test",
            agenda_context="test",
            _injected_runner=runner,
        )

        assert result.success is False
        assert "exited with code 1" in result.error_message
        assert result.opinion_packet is None

    def test_llm_timeout_causes_failure(
        self, art_director_persona: PersonaDefinition
    ) -> None:
        """Timeout (exit_code=-1) should produce success=False."""
        runner = _make_mock_runner(-1, "", "TimeoutExpired: timed out after 120s")
        result = generate_opinion(
            persona=art_director_persona,
            agenda_item_ref="test",
            agenda_context="test",
            _injected_runner=runner,
        )

        assert result.success is False
        assert "timed out" in result.error_message

    def test_bad_json_response_causes_failure(
        self, art_director_persona: PersonaDefinition
    ) -> None:
        """Malformed JSON in LLM output should fail at parse stage."""
        runner = _make_mock_runner(0, "this is not json at all")
        result = generate_opinion(
            persona=art_director_persona,
            agenda_item_ref="test",
            agenda_context="test",
            _injected_runner=runner,
        )

        assert result.success is False
        assert "Response parsing failed" in result.error_message

    def test_raw_llm_output_preserved_on_failure(
        self, art_director_persona: PersonaDefinition
    ) -> None:
        """raw_llm_output should be preserved even on failure (for debugging)."""
        bad_output = "garbage response from LLM"
        runner = _make_mock_runner(0, bad_output)
        result = generate_opinion(
            persona=art_director_persona,
            agenda_item_ref="test",
            agenda_context="test",
            _injected_runner=runner,
        )

        assert result.success is False
        assert result.raw_llm_output == bad_output

    # ── Input validation ──

    def test_non_persona_definition_raises_type_error(self) -> None:
        """Non-PersonaDefinition input must raise TypeError."""
        with pytest.raises(TypeError, match="must be PersonaDefinition"):
            generate_opinion(
                persona="not-a-persona",  # type: ignore[arg-type]
                agenda_item_ref="test",
                agenda_context="test",
            )

    def test_empty_agenda_item_ref_raises_value_error(
        self, art_director_persona: PersonaDefinition
    ) -> None:
        """Empty agenda_item_ref must raise ValueError."""
        with pytest.raises(ValueError, match="agenda_item_ref must be"):
            generate_opinion(
                persona=art_director_persona,
                agenda_item_ref="",
                agenda_context="test",
            )

    def test_empty_agenda_context_raises_value_error(
        self, art_director_persona: PersonaDefinition
    ) -> None:
        """Empty agenda_context must raise ValueError."""
        with pytest.raises(ValueError, match="agenda_context must be"):
            generate_opinion(
                persona=art_director_persona,
                agenda_item_ref="test",
                agenda_context="",
            )

    # ── Result immutability ──

    def test_result_is_frozen(
        self,
        art_director_persona: PersonaDefinition,
        valid_opinion_json: str,
    ) -> None:
        """OpinionGenerationResult must be immutable."""
        runner = _make_mock_runner(0, valid_opinion_json)
        result = generate_opinion(
            persona=art_director_persona,
            agenda_item_ref="test",
            agenda_context="test",
            _injected_runner=runner,
        )
        with pytest.raises(Exception):
            result.success = False  # type: ignore[misc]

    # ── Runner injection ──

    def test_injected_runner_receives_command(
        self,
        art_director_persona: PersonaDefinition,
        valid_opinion_json: str,
    ) -> None:
        """The mock runner must receive a valid opencode-go command."""
        runner = _make_mock_runner(0, valid_opinion_json)
        generate_opinion(
            persona=art_director_persona,
            agenda_item_ref="test-item",
            agenda_context="test context",
            _injected_runner=runner,
        )

        assert len(runner.calls) == 1  # type: ignore[attr-defined]
        cmd = runner.calls[0]["command"]  # type: ignore[attr-defined]
        assert cmd[0] == "opencode-go"
        assert "--model" in cmd
        assert "--context-file" in cmd
        assert cmd[2] == "qwen-max"

    def test_injected_runner_receives_timeout(
        self,
        art_director_persona: PersonaDefinition,
        valid_opinion_json: str,
    ) -> None:
        """Timeout from config must be forwarded to runner."""
        runner = _make_mock_runner(0, valid_opinion_json)
        generate_opinion(
            persona=art_director_persona,
            agenda_item_ref="test",
            agenda_context="test",
            timeout_seconds=60.0,
            _injected_runner=runner,
        )

        assert runner.calls[0]["timeout_seconds"] == 60.0  # type: ignore[attr-defined]

    # ── Multi-persona verification ──

    def test_multiple_persona_types_all_succeed(self) -> None:
        """Leader, worker, and executor personas should all generate opinions."""
        personas = [
            PersonaDefinition(
                role_id="content-director",
                display_name="Content Director",
                team="content-production",
                role_type="leader",
                expertise_tags=("creative_direction",),
                model_name="qwen-max",
            ),
            PersonaDefinition(
                role_id="scriptwriter",
                display_name="Scriptwriter",
                team="content-production",
                role_type="worker",
                expertise_tags=("scriptwriting",),
                model_name="qwen-max",
            ),
            PersonaDefinition(
                role_id="validator",
                display_name="Validator",
                team="validation",
                role_type="validator",
                expertise_tags=("validation", "risk_assessment"),
                model_name="glm-5.1",
            ),
        ]

        for persona in personas:
            valid_json = json.dumps(
                {
                    "persona_id": persona.role_id,
                    "agenda_item_ref": "test-agenda",
                    "opinion_content": f"Opinion from {persona.display_name}",
                    "confidence": 0.80,
                    "timestamp": "2026-06-10T14:30:00Z",
                }
            )
            runner = _make_mock_runner(0, valid_json)
            result = generate_opinion(
                persona=persona,
                agenda_item_ref="test-agenda",
                agenda_context="test meeting",
                _injected_runner=runner,
            )
            assert result.success is True, (
                f"Persona {persona.role_id} ({persona.role_type}) "
                f"should succeed"
            )
            assert result.role_id == persona.role_id
            assert result.opinion_packet is not None

    # ── Global runner injection ──

    def test_inject_runner_module_level(
        self,
        art_director_persona: PersonaDefinition,
        valid_opinion_json: str,
    ) -> None:
        """Module-level inject_runner should replace the default runner."""
        original = get_runner()

        try:
            runner = _make_mock_runner(0, valid_opinion_json)
            inject_runner(runner)
            assert get_runner() is runner

            result = generate_opinion(
                persona=art_director_persona,
                agenda_item_ref="test",
                agenda_context="test",
                # No _injected_runner — uses module-level runner
            )
            assert result.success is True
            assert len(runner.calls) == 1  # type: ignore[attr-defined]
        finally:
            inject_runner(original)

    def test_inject_runner_none_restores_default(self) -> None:
        """Passing None to inject_runner restores the production runner."""
        inject_runner(None)
        runner = get_runner()
        assert runner is _default_subprocess_runner

    # ── Fallback model not called on success ──

    def test_fallback_not_called_on_success(
        self,
        art_director_persona: PersonaDefinition,
        valid_opinion_json: str,
    ) -> None:
        """Primary model success should NOT invoke fallback model."""
        runner = _make_mock_runner(0, valid_opinion_json)
        result = generate_opinion(
            persona=art_director_persona,
            agenda_item_ref="test",
            agenda_context="test",
            _injected_runner=runner,
        )

        assert result.success is True
        assert len(runner.calls) == 1  # type: ignore[attr-defined]
        # Primary model was used
        assert runner.calls[0]["command"][2] == "qwen-max"  # type: ignore[attr-defined]

    # ── Stderr on success preserved ──

    def test_stderr_warning_on_success_not_fatal(
        self,
        art_director_persona: PersonaDefinition,
        valid_opinion_json: str,
    ) -> None:
        """Stderr warning on success should not cause failure."""
        runner = _make_mock_runner(
            0, valid_opinion_json, stderr="Warning: rate limit approaching"
        )
        result = generate_opinion(
            persona=art_director_persona,
            agenda_item_ref="test",
            agenda_context="test",
            _injected_runner=runner,
        )
        # Stderr on exit_code=0 is a warning, not an error
        assert result.success is True


# ═════════════════════════════════════════════════════════════════════════
# 6. Cross-persona isolation enforcement (Sub-AC 5a-3)
# ═════════════════════════════════════════════════════════════════════════


class TestSequentialIsolation:
    """Verify that sequential persona generations do not leak state or
    opinion data between persona contexts."""

    def test_sequential_personas_produce_independent_prompts(
        self,
        art_director_persona: PersonaDefinition,
        tech_director_persona: PersonaDefinition,
    ) -> None:
        """Persona A's prompt must not contain persona B's identity data."""
        prompt_a = build_opinion_prompt(
            art_director_persona,
            "character-visual-concept",
            "Character design discussion",
        )
        prompt_b = build_opinion_prompt(
            tech_director_persona,
            "architecture-review",
            "System architecture review",
        )

        # Art director prompt must NOT contain tech director identity
        assert "tech-director" not in prompt_a
        assert "테크 디렉터" not in prompt_a
        assert "tech-engineering" not in prompt_a
        assert "deepseek-v3" not in prompt_a

        # Tech director prompt must NOT contain art director identity
        assert "art-director" not in prompt_b
        assert "아트 디렉터" not in prompt_b
        assert "art-design" not in prompt_b
        assert "visual_direction" not in prompt_b

    def test_sequential_generations_produce_independent_results(
        self,
        art_director_persona: PersonaDefinition,
        tech_director_persona: PersonaDefinition,
    ) -> None:
        """Results from persona A must not leak into persona B's generation."""
        valid_a = json.dumps(
            {
                "persona_id": "art-director",
                "agenda_item_ref": "character-concept",
                "opinion_content": "Neon-noir palette with purple-cyan gradients.",
                "confidence": 0.88,
                "timestamp": "2026-06-10T14:30:00Z",
            }
        )
        valid_b = json.dumps(
            {
                "persona_id": "tech-director",
                "agenda_item_ref": "architecture-review",
                "opinion_content": "Microservices with event-driven communication.",
                "confidence": 0.90,
                "timestamp": "2026-06-10T14:30:00Z",
            }
        )

        runner_a = _make_tracking_runner(valid_a)
        runner_b = _make_tracking_runner(valid_b)

        # Generate persona A first
        result_a = generate_opinion(
            persona=art_director_persona,
            agenda_item_ref="character-concept",
            agenda_context="Character design",
            _injected_runner=runner_a,
        )
        assert result_a.success is True
        assert result_a.role_id == "art-director"
        assert "Neon-noir" in result_a.opinion_packet["opinion_content"]  # type: ignore[index]

        # Generate persona B second — must be fully independent
        result_b = generate_opinion(
            persona=tech_director_persona,
            agenda_item_ref="architecture-review",
            agenda_context="System architecture",
            _injected_runner=runner_b,
        )
        assert result_b.success is True
        assert result_b.role_id == "tech-director"
        assert "Microservices" in result_b.opinion_packet["opinion_content"]  # type: ignore[index]

        # Cross-verify: A's result has no B data and vice versa
        assert result_a.role_id != result_b.role_id
        assert result_a.model_name == "qwen-max"
        assert result_b.model_name == "deepseek-v3"
        assert "tech-director" not in result_a.raw_llm_output
        assert "art-director" not in result_b.raw_llm_output

    def test_sequential_runner_tracking_is_independent(
        self,
        art_director_persona: PersonaDefinition,
        tech_director_persona: PersonaDefinition,
    ) -> None:
        """Each persona's mock runner must only see its own command."""
        valid_json = json.dumps(
            {
                "persona_id": "test-role",
                "agenda_item_ref": "test",
                "opinion_content": "Test opinion",
                "confidence": 0.5,
                "timestamp": "2026-06-10T14:30:00Z",
            }
        )

        runner_a = _make_tracking_runner(valid_json)
        runner_b = _make_tracking_runner(valid_json)

        generate_opinion(
            persona=art_director_persona,
            agenda_item_ref="test-a",
            agenda_context="Context A",
            _injected_runner=runner_a,
        )
        generate_opinion(
            persona=tech_director_persona,
            agenda_item_ref="test-b",
            agenda_context="Context B",
            _injected_runner=runner_b,
        )

        # Runner A should have received art-director model
        assert len(runner_a.calls) == 1  # type: ignore[attr-defined]
        cmd_a = runner_a.calls[0]["command"]  # type: ignore[attr-defined]
        assert "qwen-max" in cmd_a

        # Runner B should have received tech-director model
        assert len(runner_b.calls) == 1  # type: ignore[attr-defined]
        cmd_b = runner_b.calls[0]["command"]  # type: ignore[attr-defined]
        assert "deepseek-v3" in cmd_b

        # Each runner received exactly one call — no cross-contamination
        assert cmd_a != cmd_b


class TestConcurrentIsolation:
    """Verify that concurrent persona generations do not leak state or
    opinion data between persona contexts when run in threads."""

    @staticmethod
    def _run_generation(
        persona: PersonaDefinition,
        agenda_item_ref: str,
        agenda_context: str,
        mock_stdout: str,
        results: list,
        index: int,
    ) -> None:
        """Helper: run a generation in a thread and store the result."""
        runner = _make_tracking_runner(mock_stdout)
        result = generate_opinion(
            persona=persona,
            agenda_item_ref=agenda_item_ref,
            agenda_context=agenda_context,
            _injected_runner=runner,
        )
        results[index] = (result, runner)

    def test_concurrent_generations_are_independent(
        self,
        art_director_persona: PersonaDefinition,
        tech_director_persona: PersonaDefinition,
    ) -> None:
        """Two concurrent persona generations must not interfere."""
        import threading

        valid_a = json.dumps(
            {
                "persona_id": "art-director",
                "agenda_item_ref": "character-concept",
                "opinion_content": "Art director opinion: neon-noir.",
                "confidence": 0.88,
                "timestamp": "2026-06-10T14:30:00Z",
            }
        )
        valid_b = json.dumps(
            {
                "persona_id": "tech-director",
                "agenda_item_ref": "architecture-review",
                "opinion_content": "Tech director opinion: microservices.",
                "confidence": 0.90,
                "timestamp": "2026-06-10T14:30:00Z",
            }
        )

        results: list = [None, None]  # type: ignore[assignment]

        t1 = threading.Thread(
            target=self._run_generation,
            args=(
                art_director_persona,
                "character-concept",
                "Character design discussion",
                valid_a,
                results,
                0,
            ),
        )
        t2 = threading.Thread(
            target=self._run_generation,
            args=(
                tech_director_persona,
                "architecture-review",
                "Architecture review discussion",
                valid_b,
                results,
                1,
            ),
        )

        t1.start()
        t2.start()
        t1.join()
        t2.join()

        result_a, runner_a = results[0]
        result_b, runner_b = results[1]

        # Both must have succeeded
        assert result_a.success is True, f"Persona A failed: {result_a.error_message}"
        assert result_b.success is True, f"Persona B failed: {result_b.error_message}"

        # Identity independence
        assert result_a.role_id == "art-director"
        assert result_b.role_id == "tech-director"

        # Model independence
        assert result_a.model_name == "qwen-max"
        assert result_b.model_name == "deepseek-v3"

        # Content independence — A's content must NOT appear in B's result
        assert "neon-noir" in result_a.opinion_packet["opinion_content"]  # type: ignore[index]
        assert "microservices" in result_b.opinion_packet["opinion_content"]  # type: ignore[index]
        assert "neon-noir" not in result_b.raw_llm_output
        assert "microservices" not in result_a.raw_llm_output

        # Runner independence — each runner received exactly one call
        assert len(runner_a.calls) == 1  # type: ignore[attr-defined]
        assert len(runner_b.calls) == 1  # type: ignore[attr-defined]

        # Each runner received the correct model
        assert "qwen-max" in runner_a.calls[0]["command"]  # type: ignore[attr-defined]
        assert "deepseek-v3" in runner_b.calls[0]["command"]  # type: ignore[attr-defined]

    def test_concurrent_many_personas_all_independent(self) -> None:
        """Multiple concurrent persona generations must all be independent."""
        import threading

        roles = [
            ("art-director", "qwen-max", "Neon-noir palette recommendation"),
            ("tech-director", "deepseek-v3", "Microservices architecture"),
            ("sns-strategist", "qwen-max", "TikTok-first content strategy"),
            ("scriptwriter", "deepseek-v3", "Three-act narrative structure"),
        ]

        results: list = [None] * len(roles)  # type: ignore[assignment]
        threads: list[threading.Thread] = []

        for i, (role_id, model, opinion) in enumerate(roles):
            persona = PersonaDefinition(
                role_id=role_id,
                display_name=role_id.replace("-", " ").title(),
                team="test-team",
                expertise_tags=("testing",),
                model_name=model,
            )
            valid_json = json.dumps(
                {
                    "persona_id": role_id,
                    "agenda_item_ref": f"test-{i}",
                    "opinion_content": opinion,
                    "confidence": 0.80,
                    "timestamp": "2026-06-10T14:30:00Z",
                }
            )
            t = threading.Thread(
                target=self._run_generation,
                args=(persona, f"test-{i}", f"Context {i}", valid_json, results, i),
            )
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        # Verify all succeeded
        for i, (role_id, model, opinion) in enumerate(roles):
            result, runner = results[i]
            assert result.success is True, (
                f"Role {role_id} failed: {result.error_message}"
            )
            assert result.role_id == role_id
            assert result.model_name == model
            assert opinion in result.opinion_packet["opinion_content"]  # type: ignore[index]

            # Each runner received exactly one call
            assert len(runner.calls) == 1  # type: ignore[attr-defined]

        # Cross-verify: each result's raw_llm_output contains only its own opinion
        for i in range(len(roles)):
            _, opinion_i = roles[i][0], roles[i][2]
            for j in range(len(roles)):
                _, opinion_j = roles[j][0], roles[j][2]
                result_j, _ = results[j]
                if i != j:
                    # Result j's output must NOT contain role i's opinion
                    assert opinion_i not in result_j.raw_llm_output, (
                        f"Leak detected: role {roles[i][0]} opinion found "
                        f"in role {roles[j][0]} output"
                    )


class TestModuleStateIsolation:
    """Verify that module-level state (runner injection) is properly
    isolated across threads and does not leak."""

    def test_thread_local_runner_injection_is_isolated(
        self,
        art_director_persona: PersonaDefinition,
        tech_director_persona: PersonaDefinition,
        valid_opinion_json: str,
    ) -> None:
        """Injecting a runner in thread A must not affect thread B."""
        import threading

        results: dict[str, object] = {}

        def thread_a():
            runner = _make_tracking_runner(valid_opinion_json)
            inject_runner(runner)
            # Verify thread A sees its own runner
            assert get_runner() is runner
            result = generate_opinion(
                persona=art_director_persona,
                agenda_item_ref="test-a",
                agenda_context="Context A",
            )
            results["a_runner"] = runner
            results["a_result"] = result
            results["a_runner_is_injected"] = get_runner() is runner

        def thread_b():
            # Thread B should NOT see thread A's runner
            default = get_runner()
            results["b_default_runner"] = default
            result = generate_opinion(
                persona=tech_director_persona,
                agenda_item_ref="test-b",
                agenda_context="Context B",
                _injected_runner=_make_tracking_runner(valid_opinion_json),
            )
            results["b_result"] = result

        ta = threading.Thread(target=thread_a)
        tb = threading.Thread(target=thread_b)
        ta.start()
        tb.start()
        ta.join()
        tb.join()

        # Thread A's injected runner was used
        assert results["a_runner_is_injected"] is True

        # Thread B should have seen the default runner (not A's)
        b_default = results["b_default_runner"]
        assert b_default is not results["a_runner"], (
            "Thread B leaked thread A's injected runner"
        )

        # Both generations succeeded
        assert results["a_result"].success is True  # type: ignore[attr-defined]
        assert results["b_result"].success is True  # type: ignore[attr-defined]

    def test_restore_default_after_inject(self) -> None:
        """inject_runner(None) must restore the default runner."""
        original = get_runner()

        # Inject a mock
        runner = _make_tracking_runner("{}")
        inject_runner(runner)
        assert get_runner() is runner

        # Restore default
        inject_runner(None)
        restored = get_runner()
        # After restoration, get_runner() returns via _get_default_runner()
        # which returns _default_subprocess_runner
        from src.persona_opinion_generator import _default_subprocess_runner

        assert restored is _default_subprocess_runner
        # Clean up
        inject_runner(original if original is not _default_subprocess_runner else None)

    def test_multiple_inject_restore_cycles(self) -> None:
        """Multiple inject/restore cycles must work correctly on same thread."""
        # Start clean
        inject_runner(None)

        for i in range(5):
            runner = _make_tracking_runner("{}")
            inject_runner(runner)
            assert get_runner() is runner
            inject_runner(None)
            from src.persona_opinion_generator import _default_subprocess_runner

            assert get_runner() is _default_subprocess_runner


class TestOutputIndependence:
    """Verify that OpinionGenerationResult instances are fully independent
    and correctly scoped per persona."""

    def test_result_fields_are_correctly_scoped(
        self,
        art_director_persona: PersonaDefinition,
        tech_director_persona: PersonaDefinition,
    ) -> None:
        """Each result must carry only its own persona's identity fields."""
        valid_a = json.dumps(
            {
                "persona_id": "art-director",
                "agenda_item_ref": "item-a",
                "opinion_content": "Opinion A",
                "confidence": 0.7,
                "timestamp": "2026-06-10T14:30:00Z",
            }
        )
        valid_b = json.dumps(
            {
                "persona_id": "tech-director",
                "agenda_item_ref": "item-b",
                "opinion_content": "Opinion B",
                "confidence": 0.8,
                "timestamp": "2026-06-10T14:30:00Z",
            }
        )

        result_a = generate_opinion(
            persona=art_director_persona,
            agenda_item_ref="item-a",
            agenda_context="Context A",
            _injected_runner=_make_tracking_runner(valid_a),
        )
        result_b = generate_opinion(
            persona=tech_director_persona,
            agenda_item_ref="item-b",
            agenda_context="Context B",
            _injected_runner=_make_tracking_runner(valid_b),
        )

        # Identity scoping
        assert result_a.role_id == "art-director"
        assert result_b.role_id == "tech-director"
        assert result_a.model_name == "qwen-max"
        assert result_b.model_name == "deepseek-v3"

        # Packet scoping
        assert result_a.opinion_packet["persona_id"] == "art-director"  # type: ignore[index]
        assert result_b.opinion_packet["persona_id"] == "tech-director"  # type: ignore[index]

        # Opinion content scoping
        assert result_a.opinion_packet["opinion_content"] == "Opinion A"  # type: ignore[index]
        assert result_b.opinion_packet["opinion_content"] == "Opinion B"  # type: ignore[index]

    def test_failure_results_are_also_scoped(
        self, art_director_persona: PersonaDefinition
    ) -> None:
        """Even failed generations must carry correct persona identity."""
        runner = _make_mock_runner(1, "", "Model not found")

        result = generate_opinion(
            persona=art_director_persona,
            agenda_item_ref="test",
            agenda_context="test",
            _injected_runner=runner,
        )

        assert result.success is False
        assert result.role_id == "art-director"
        assert result.model_name == "qwen-max"
        # Failed results should not have opinion_packet
        assert result.opinion_packet is None

    def test_persona_definition_is_frozen_and_reusable(
        self, art_director_persona: PersonaDefinition
    ) -> None:
        """The same PersonaDefinition instance can be safely reused
        across multiple generate_opinion calls without mutation."""
        valid_json = json.dumps(
            {
                "persona_id": "art-director",
                "agenda_item_ref": "test",
                "opinion_content": "Reusable test.",
                "confidence": 0.5,
                "timestamp": "2026-06-10T14:30:00Z",
            }
        )

        results = []
        for i in range(10):
            runner = _make_tracking_runner(valid_json)
            result = generate_opinion(
                persona=art_director_persona,
                agenda_item_ref=f"test-{i}",
                agenda_context=f"Context {i}",
                _injected_runner=runner,
            )
            results.append(result)

        # All must succeed
        for result in results:
            assert result.success is True
            assert result.role_id == "art-director"

        # The persona definition must remain unchanged
        assert art_director_persona.role_id == "art-director"
        assert art_director_persona.model_name == "qwen-max"
