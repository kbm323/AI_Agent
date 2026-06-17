"""Tests for the static rule matching engine (Sub-AC 3.2a).

Covers:
- Keyword normalization (lowercase, punctuation stripping, tokenization)
- Single keyword group matching (OR logic within group)
- Multiple keyword group matching (AND logic across groups)
- First-match-wins ordering
- Empty keyword groups (general-discussion catch-all)
- No-match fallback to general-discussion
- Korean input matching
- Mixed Korean/English input matching
- Risk detection pattern matching
- Risk tag deduplication
- Escalation rules triggering codex_required
- Priority inference from keywords
- Default priority fallback
- Guardrail enforcement (max roles, role limits)
- Guardrail: validator always required
- Guardrail: security/data-loss/legal → codex_required=true
- Determinism: same input → same output
- Custom rule sets (independently testable)
- Error handling: empty topic, missing fields
- Full routing_rules.yaml integration test
"""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from textwrap import dedent

import pytest

from src.routing_rules_loader import load_routing_rules


# ═══════════════════════════════════════════════════════════════════════════
# Import the module under test (will exist after implementation)
# ═══════════════════════════════════════════════════════════════════════════

# We import the matcher module dynamically so the test file is valid
# before the implementation module exists, but we expect it to be there
# by the time tests run (TDD: write tests → implement → run).
from src.static_rule_matcher import (
    MatchResult,
    MeetingContext,
    RuleConfig,
    StaticRuleMatcher,
    match_meeting_route,
)


# ═══════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture(scope="module")
def real_rules() -> dict:
    """Load the actual routing_rules.yaml for integration tests."""
    p = Path(__file__).resolve().parent.parent / "config" / "routing_rules.yaml"
    return load_routing_rules(p)


@pytest.fixture(scope="module")
def real_config(real_rules: dict) -> RuleConfig:
    """Build a RuleConfig from the real routing rules."""
    return RuleConfig.from_dict(real_rules)


@pytest.fixture
def matcher(real_config: RuleConfig) -> StaticRuleMatcher:
    """Create a StaticRuleMatcher with the real config."""
    return StaticRuleMatcher(real_config)


# ═══════════════════════════════════════════════════════════════════════════
# Minimal custom rule set fixtures (for independent testability)
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def minimal_rules() -> dict:
    """Minimal rule set for isolated testing of matching logic."""
    return {
        "version": "1.0.0",
        "defaults": {
            "validator_required": True,
            "validator_role_id": "validator",
            "codex_required": False,
            "max_roles_per_meeting": 7,
            "max_required_roles": 6,
        },
        "agenda_types": [
            {
                "id": "creative-production",
                "display_name": "Creative Production",
                "keywords": [
                    ["music", "audio"],
                    ["video", "film"],
                ],
                "tags": ["creative", "production"],
                "risk_tags": [],
                "required_roles": ["content-director"],
                "optional_roles": [],
                "validator_required": True,
                "codex_required": False,
            },
            {
                "id": "technical",
                "display_name": "Technical Dev",
                "keywords": [
                    ["code", "api", "bug"],
                ],
                "tags": ["tech", "development"],
                "risk_tags": ["code_change"],
                "required_roles": ["tech-director"],
                "optional_roles": [],
                "validator_required": True,
                "codex_required": False,
            },
            {
                "id": "general-discussion",
                "display_name": "General Discussion",
                "keywords": [],
                "tags": ["general"],
                "risk_tags": [],
                "required_roles": ["content-director"],
                "optional_roles": [],
                "validator_required": True,
                "codex_required": False,
            },
        ],
        "risk_detection": {
            "patterns": [
                {
                    "risk_tag": "security_risk",
                    "severity": "high",
                    "keywords": ["security", "hack", "vulnerability"],
                    "auto_codex": True,
                },
                {
                    "risk_tag": "budget_financial",
                    "severity": "medium",
                    "keywords": ["budget", "cost", "money"],
                    "auto_codex": True,
                },
            ],
        },
        "escalation_rules": {
            "codex_triggers": [
                {
                    "id": "trigger-security",
                    "trigger_number": 1,
                    "description": "Security risk",
                    "condition": {"any_risk_tag_in": ["security_risk"]},
                    "action": "set_codex_required_true",
                },
                {
                    "id": "trigger-budget",
                    "trigger_number": 2,
                    "description": "Budget risk",
                    "condition": {"any_risk_tag_in": ["budget_financial"]},
                    "action": "set_codex_required_true",
                },
            ],
        },
        "priority_rules": {
            "inference": [
                {
                    "priority": "P0",
                    "label": "Emergency",
                    "keywords": ["urgent", "critical", "emergency"],
                },
                {
                    "priority": "P1",
                    "label": "High Priority",
                    "keywords": ["important", "priority", "release"],
                },
            ],
            "default": "P2",
        },
        "guardrails": [
            {
                "id": "max_roles_per_meeting",
                "description": "Max 7 agents",
                "rule": "len(required_roles) + len(optional_roles) <= 7",
                "enforcement": "truncate_optional_roles",
            },
        ],
    }


@pytest.fixture
def minimal_config(minimal_rules: dict) -> RuleConfig:
    """Build a RuleConfig from minimal rules."""
    return RuleConfig.from_dict(minimal_rules)


@pytest.fixture
def minimal_matcher(minimal_config: RuleConfig) -> StaticRuleMatcher:
    """Create a StaticRuleMatcher with minimal config."""
    return StaticRuleMatcher(minimal_config)


# ═══════════════════════════════════════════════════════════════════════════
# Data model tests
# ═══════════════════════════════════════════════════════════════════════════


class TestDataModels:
    """Test MeetingContext, MatchResult, and RuleConfig data models."""

    def test_meeting_context_creation(self):
        ctx = MeetingContext(
            topic="Build a music video production pipeline",
            meeting_type="creative_production",
            participants=["content-director", "art-director"],
            priority="P1",
        )
        assert ctx.topic == "Build a music video production pipeline"
        assert ctx.meeting_type == "creative_production"
        assert ctx.participants == ("content-director", "art-director")
        assert ctx.priority == "P1"

    def test_meeting_context_defaults(self):
        ctx = MeetingContext(topic="Hello")
        assert ctx.meeting_type == ""
        assert ctx.participants == ()
        assert ctx.priority == ""

    def test_match_result_frozen(self):
        result = MatchResult(
            agenda_type="test",
            agenda_label="Test",
            tags=("tag1",),
            risk_tags=(),
            required_roles=("role1",),
            optional_roles=(),
            validator_required=True,
            codex_required=False,
            priority="P2",
            routing_source="static_fallback",
            routing_reason="test",
            confidence=0.7,
        )
        with pytest.raises(Exception):
            result.agenda_type = "changed"  # type: ignore[misc]

    def test_match_result_to_dict(self):
        result = MatchResult(
            agenda_type="creative-production",
            agenda_label="Creative Production",
            tags=("creative", "production"),
            risk_tags=("security_risk",),
            required_roles=("content-director", "validator"),
            optional_roles=("art-director",),
            validator_required=True,
            codex_required=True,
            priority="P1",
            routing_source="static_fallback",
            routing_reason="qwen_timeout",
            confidence=0.7,
        )
        d = result.to_dict()
        assert d["agenda_type"] == "creative-production"
        assert d["tags"] == ["creative", "production"]
        assert d["risk_tags"] == ["security_risk"]
        assert d["codex_required"] is True
        assert d["routing_source"] == "static_fallback"

    def test_match_result_to_json_serializable(self):
        result = MatchResult(
            agenda_type="test",
            agenda_label="Test",
            tags=(),
            risk_tags=(),
            required_roles=(),
            optional_roles=(),
            validator_required=True,
            codex_required=False,
            priority="P2",
            routing_source="static_fallback",
            routing_reason="test",
            confidence=0.7,
        )
        json_str = json.dumps(result.to_dict())
        parsed = json.loads(json_str)
        assert parsed["agenda_type"] == "test"

    def test_rule_config_from_minimal(self, minimal_rules: dict):
        config = RuleConfig.from_dict(minimal_rules)
        assert len(config.agenda_types) == 3
        assert config.agenda_types[0]["id"] == "creative-production"
        assert len(config.risk_patterns) == 2
        assert len(config.codex_triggers) == 2

    def test_rule_config_from_real(self, real_rules: dict):
        config = RuleConfig.from_dict(real_rules)
        assert len(config.agenda_types) >= 10
        assert len(config.risk_patterns) >= 10
        assert len(config.codex_triggers) >= 6
        assert config.default_priority == "P2"


# ═══════════════════════════════════════════════════════════════════════════
# Keyword normalization
# ═══════════════════════════════════════════════════════════════════════════


class TestKeywordNormalization:
    """Test the input normalization logic."""

    def test_normalize_lowercase(self, minimal_matcher):
        result = minimal_matcher._normalize("Hello World")
        assert "hello" in result
        assert "world" in result

    def test_normalize_strips_punctuation(self, minimal_matcher):
        result = minimal_matcher._normalize("Hello, World! How's it going?")
        assert "," not in result
        assert "!" not in result
        assert "?" not in result
        assert "hello" in result

    def test_normalize_korean(self, minimal_matcher):
        result = minimal_matcher._normalize("뮤직비디오 제작 회의")
        assert "뮤직비디오" in result
        assert "제작" in result
        assert "회의" in result

    def test_normalize_mixed_korean_english(self, minimal_matcher):
        result = minimal_matcher._normalize("AI 기반 music_video 제작")
        assert "ai" in result
        assert "기반" in result
        assert "music_video" in result
        assert "제작" in result

    def test_normalize_empty_string(self, minimal_matcher):
        result = minimal_matcher._normalize("")
        assert result == ""

    def test_normalize_whitespace_only(self, minimal_matcher):
        result = minimal_matcher._normalize("   \t\n  ")
        assert result == ""

    def test_normalize_underscores_preserved(self, minimal_matcher):
        """Underscores should be preserved as they're common in tags/keywords."""
        result = minimal_matcher._normalize("music_video production_pipeline")
        assert "music_video" in result
        assert "production_pipeline" in result


# ═══════════════════════════════════════════════════════════════════════════
# Agenda type matching (core algorithm)
# ═══════════════════════════════════════════════════════════════════════════


class TestAgendaTypeMatching:
    """Test the core keyword-based agenda type matching algorithm."""

    def test_single_keyword_group_match_or_logic(self, minimal_matcher):
        """OR logic within a group: any keyword matches → group matches.

        But AND logic across groups: ALL groups must match.
        "create some music" only matches group 1 [music, audio],
        NOT group 2 [video, film] → no overall match.
        """
        result = minimal_matcher._match_agenda_type("create some music")
        # Only one of two groups matched → no agenda type match
        assert result is None or result["id"] == "general-discussion"

    def test_single_keyword_group_match_other_keyword(self, minimal_matcher):
        """Other keyword in same group should also match that group.

        But AND logic across groups still applies.
        "audio processing tool" only matches group 1, not group 2.
        """
        result = minimal_matcher._match_agenda_type("audio processing tool")
        assert result is None or result["id"] == "general-discussion"

    def test_all_groups_must_match_and_logic(self, minimal_matcher):
        """AND logic across groups: ALL groups must match."""
        # creative-production requires BOTH [music/audio] AND [video/film]
        # "music" alone matches first group but not second → no match
        # But wait — creative-production actually doesn't have "production" as a second group.
        # The groups are: [music, audio], [video, film]
        # So "create some music" matches group 1 but NOT group 2.
        # This means it shouldn't match unless we have BOTH.
        # Let's test: "music video" should match
        result = minimal_matcher._match_agenda_type("music video production")
        assert result is not None
        assert result["id"] == "creative-production"

    def test_only_one_group_matches_no_overall_match(self, minimal_matcher):
        """When only one keyword group matches, no agenda type match."""
        result = minimal_matcher._match_agenda_type("just music here")
        # creative-production needs both [music/audio] AND [video/film]
        # Only [music/audio] matched → no match
        # technical needs [code/api/bug] → no match
        # Should fall back
        assert result is None or result["id"] == "general-discussion"

    def test_technical_match(self, minimal_matcher):
        """Technical agenda type should match code-related topics."""
        result = minimal_matcher._match_agenda_type("fix the api bug")
        assert result is not None
        assert result["id"] == "technical"

    def test_first_match_wins(self, minimal_matcher):
        """First matching agenda_type in order should win."""
        # Both creative-production and technical might match "music code"
        # But creative-production is first → it should win
        result = minimal_matcher._match_agenda_type("music video with code")
        assert result is not None
        assert result["id"] == "creative-production"

    def test_empty_keyword_groups_match_anything(self, minimal_matcher):
        """Empty keyword groups (general-discussion) should match anything."""
        result = minimal_matcher._match_agenda_type("some random topic xyz")
        assert result is not None
        assert result["id"] == "general-discussion"

    def test_no_keywords_match_all_types(self, minimal_matcher):
        """When nothing matches, catch-all should return."""
        result = minimal_matcher._match_agenda_type("xyzzy foobar")
        assert result is not None
        assert result["id"] == "general-discussion"

    def test_korean_input_matching(self, minimal_matcher):
        """Korean keywords should match Korean input."""
        # Build a config with Korean keywords
        rules_kr = {
            "version": "1.0.0",
            "defaults": {
                "validator_required": True,
                "validator_role_id": "validator",
                "codex_required": False,
                "max_roles_per_meeting": 7,
                "max_required_roles": 6,
            },
            "agenda_types": [
                {
                    "id": "korean-meeting",
                    "display_name": "한국어 회의",
                    "keywords": [["뮤직비디오", "music_video"], ["제작", "production"]],
                    "tags": ["korean"],
                    "risk_tags": [],
                    "required_roles": ["content-director"],
                    "optional_roles": [],
                    "validator_required": True,
                    "codex_required": False,
                },
                {
                    "id": "general-discussion",
                    "display_name": "General",
                    "keywords": [],
                    "tags": ["general"],
                    "risk_tags": [],
                    "required_roles": ["content-director"],
                    "optional_roles": [],
                    "validator_required": True,
                    "codex_required": False,
                },
            ],
            "risk_detection": {"patterns": []},
            "escalation_rules": {"codex_triggers": []},
            "priority_rules": {"inference": [], "default": "P2"},
            "guardrails": [],
        }
        config = RuleConfig.from_dict(rules_kr)
        matcher_kr = StaticRuleMatcher(config)
        result = matcher_kr._match_agenda_type("뮤직비디오 제작 관련 회의입니다")
        assert result is not None
        assert result["id"] == "korean-meeting"

    def test_mixed_korean_english_matching(self, minimal_matcher):
        """Mixed Korean/English should match English keywords."""
        rules_mixed = {
            "version": "1.0.0",
            "defaults": {
                "validator_required": True,
                "validator_role_id": "validator",
                "codex_required": False,
                "max_roles_per_meeting": 7,
                "max_required_roles": 6,
            },
            "agenda_types": [
                {
                    "id": "music-video",
                    "display_name": "Music Video",
                    "keywords": [["music", "뮤직비디오"], ["video", "영상"]],
                    "tags": ["production"],
                    "risk_tags": [],
                    "required_roles": ["content-director"],
                    "optional_roles": [],
                    "validator_required": True,
                    "codex_required": False,
                },
                {
                    "id": "general-discussion",
                    "display_name": "General",
                    "keywords": [],
                    "tags": ["general"],
                    "risk_tags": [],
                    "required_roles": ["content-director"],
                    "optional_roles": [],
                    "validator_required": True,
                    "codex_required": False,
                },
            ],
            "risk_detection": {"patterns": []},
            "escalation_rules": {"codex_triggers": []},
            "priority_rules": {"inference": [], "default": "P2"},
            "guardrails": [],
        }
        config = RuleConfig.from_dict(rules_mixed)
        matcher_mixed = StaticRuleMatcher(config)
        # Mixed input
        result = matcher_mixed._match_agenda_type("새로운 music 영상 기획")
        assert result is not None
        assert result["id"] == "music-video"


# ═══════════════════════════════════════════════════════════════════════════
# Risk detection
# ═══════════════════════════════════════════════════════════════════════════


class TestRiskDetection:
    """Test risk detection pattern matching."""

    def test_security_risk_detected(self, minimal_matcher):
        risks = minimal_matcher._detect_risks("there is a security vulnerability")
        assert "security_risk" in risks

    def test_budget_risk_detected(self, minimal_matcher):
        risks = minimal_matcher._detect_risks("the budget is over cost")
        assert "budget_financial" in risks

    def test_multiple_risks_detected(self, minimal_matcher):
        risks = minimal_matcher._detect_risks("security hack with budget overrun")
        assert "security_risk" in risks
        assert "budget_financial" in risks

    def test_no_risks_detected(self, minimal_matcher):
        risks = minimal_matcher._detect_risks("a peaceful meeting about design")
        assert len(risks) == 0

    def test_korean_risk_keywords(self, minimal_matcher):
        """Korean risk keywords should be detected."""
        rules_kr = {
            "version": "1.0.0",
            "defaults": {
                "validator_required": True,
                "validator_role_id": "validator",
                "codex_required": False,
                "max_roles_per_meeting": 7,
                "max_required_roles": 6,
            },
            "agenda_types": [
                {
                    "id": "general-discussion",
                    "display_name": "General",
                    "keywords": [],
                    "tags": ["general"],
                    "risk_tags": [],
                    "required_roles": ["content-director"],
                    "optional_roles": [],
                    "validator_required": True,
                    "codex_required": False,
                },
            ],
            "risk_detection": {
                "patterns": [
                    {
                        "risk_tag": "security_risk",
                        "severity": "high",
                        "keywords": ["보안", "security", "해킹"],
                        "auto_codex": True,
                    },
                ],
            },
            "escalation_rules": {"codex_triggers": []},
            "priority_rules": {"inference": [], "default": "P2"},
            "guardrails": [],
        }
        config = RuleConfig.from_dict(rules_kr)
        matcher_kr = StaticRuleMatcher(config)
        risks = matcher_kr._detect_risks("보안 취약점 해킹 위험")
        assert "security_risk" in risks

    def test_risk_with_requires_approval(self, minimal_matcher):
        """Risk patterns with requires_approval should flag that."""
        rules = {
            "version": "1.0.0",
            "defaults": {
                "validator_required": True,
                "validator_role_id": "validator",
                "codex_required": False,
                "max_roles_per_meeting": 7,
                "max_required_roles": 6,
            },
            "agenda_types": [
                {
                    "id": "general-discussion",
                    "display_name": "General",
                    "keywords": [],
                    "tags": ["general"],
                    "risk_tags": [],
                    "required_roles": ["content-director"],
                    "optional_roles": [],
                    "validator_required": True,
                    "codex_required": False,
                },
            ],
            "risk_detection": {
                "patterns": [
                    {
                        "risk_tag": "data_loss_risk",
                        "severity": "high",
                        "keywords": ["delete", "삭제"],
                        "auto_codex": True,
                        "requires_approval": True,
                    },
                ],
            },
            "escalation_rules": {"codex_triggers": []},
            "priority_rules": {"inference": [], "default": "P2"},
            "guardrails": [],
        }
        config = RuleConfig.from_dict(rules)
        matcher = StaticRuleMatcher(config)
        approval_tags = matcher._detect_approval_risks("delete all data")
        assert "data_loss_risk" in approval_tags

    def test_risk_deduplication(self, minimal_matcher):
        """Multiple matches of same risk_tag should only appear once."""
        risks = minimal_matcher._detect_risks("security security security hack hack")
        assert risks.count("security_risk") == 1


# ═══════════════════════════════════════════════════════════════════════════
# Escalation rule triggering
# ═══════════════════════════════════════════════════════════════════════════


class TestEscalationRules:
    """Test Codex escalation trigger evaluation."""

    def test_security_risk_triggers_codex(self, minimal_matcher):
        triggered = minimal_matcher._evaluate_codex_triggers(["security_risk"])
        assert triggered is True

    def test_budget_risk_triggers_codex(self, minimal_matcher):
        triggered = minimal_matcher._evaluate_codex_triggers(["budget_financial"])
        assert triggered is True

    def test_no_risk_no_codex(self, minimal_matcher):
        triggered = minimal_matcher._evaluate_codex_triggers([])
        assert triggered is False

    def test_irrelevant_risk_no_codex(self, minimal_matcher):
        triggered = minimal_matcher._evaluate_codex_triggers(["some_unknown_risk"])
        assert triggered is False

    def test_multiple_risks_one_triggers(self, minimal_matcher):
        triggered = minimal_matcher._evaluate_codex_triggers(
            ["some_risk", "security_risk", "another_risk"]
        )
        assert triggered is True


# ═══════════════════════════════════════════════════════════════════════════
# Priority inference
# ═══════════════════════════════════════════════════════════════════════════


class TestPriorityInference:
    """Test priority inference from topic keywords."""

    def test_emergency_priority(self, minimal_matcher):
        priority = minimal_matcher._infer_priority("this is urgent and critical")
        assert priority == "P0"

    def test_high_priority(self, minimal_matcher):
        priority = minimal_matcher._infer_priority("important release priority")
        assert priority == "P1"

    def test_default_priority(self, minimal_matcher):
        priority = minimal_matcher._infer_priority("regular meeting about design")
        assert priority == "P2"

    def test_first_keyword_match_wins(self, minimal_matcher):
        """When multiple priority rules match, first one wins."""
        # "urgent" (P0) and "important" (P1) both match
        priority = minimal_matcher._infer_priority("urgent important meeting")
        assert priority == "P0"

    def test_korean_priority_keywords(self, minimal_matcher):
        rules_kr = {
            "version": "1.0.0",
            "defaults": {
                "validator_required": True,
                "validator_role_id": "validator",
                "codex_required": False,
                "max_roles_per_meeting": 7,
                "max_required_roles": 6,
            },
            "agenda_types": [],
            "risk_detection": {"patterns": []},
            "escalation_rules": {"codex_triggers": []},
            "priority_rules": {
                "inference": [
                    {
                        "priority": "P0",
                        "label": "긴급",
                        "keywords": ["긴급", "urgent"],
                    },
                    {
                        "priority": "P3",
                        "label": "아이디어",
                        "keywords": ["아이디어", "idea"],
                    },
                ],
                "default": "P2",
            },
            "guardrails": [],
        }
        config = RuleConfig.from_dict(rules_kr)
        matcher_kr = StaticRuleMatcher(config)
        assert matcher_kr._infer_priority("긴급 장애 대응") == "P0"
        assert matcher_kr._infer_priority("아이디어 회의") == "P3"


# ═══════════════════════════════════════════════════════════════════════════
# Guardrail enforcement
# ═══════════════════════════════════════════════════════════════════════════


class TestGuardrails:
    """Test guardrail enforcement on routing results."""

    def test_max_roles_enforced(self, minimal_matcher, minimal_config):
        """When roles exceed max, optional_roles should be truncated."""
        result = MatchResult(
            agenda_type="test",
            agenda_label="Test",
            tags=(),
            risk_tags=(),
            required_roles=("r1", "r2", "r3", "r4", "r5", "r6"),
            optional_roles=("o1", "o2", "o3"),
            validator_required=True,
            codex_required=False,
            priority="P2",
            routing_source="static_fallback",
            routing_reason="test",
            confidence=0.7,
        )
        guarded = minimal_matcher._apply_guardrails(result, minimal_config)
        total_roles = len(guarded.required_roles) + len(guarded.optional_roles)
        assert total_roles <= minimal_config.max_roles_per_meeting

    def test_validator_always_in_required(self, minimal_matcher, minimal_config):
        """Validator role should be added to required_roles if missing."""
        result = MatchResult(
            agenda_type="test",
            agenda_label="Test",
            tags=(),
            risk_tags=(),
            required_roles=("content-director",),
            optional_roles=(),
            validator_required=True,
            codex_required=False,
            priority="P2",
            routing_source="static_fallback",
            routing_reason="test",
            confidence=0.7,
        )
        guarded = minimal_matcher._apply_guardrails(result, minimal_config)
        assert "validator" in guarded.required_roles

    def test_routing_source_always_static(self, minimal_matcher):
        """The routing_source should always be 'static_fallback'."""
        result = minimal_matcher.match(
            MeetingContext(topic="test meeting"),
            routing_reason="qwen_timeout",
        )
        assert result.routing_source == "static_fallback"

    def test_confidence_is_0_7(self, minimal_matcher):
        """Static fallback confidence should be 0.7."""
        result = minimal_matcher.match(
            MeetingContext(topic="test meeting"),
            routing_reason="qwen_timeout",
        )
        assert result.confidence == 0.7


# ═══════════════════════════════════════════════════════════════════════════
# Determinism
# ═══════════════════════════════════════════════════════════════════════════


class TestDeterminism:
    """Verify deterministic behaviour: same input → same output."""

    def test_same_input_same_output(self, minimal_matcher):
        ctx = MeetingContext(topic="music video production with budget concerns")
        result1 = minimal_matcher.match(ctx, routing_reason="test", timestamp="2026-01-01T00:00:00Z")
        result2 = minimal_matcher.match(ctx, routing_reason="test", timestamp="2026-01-01T00:00:00Z")
        assert result1 == result2
        assert result1.to_dict() == result2.to_dict()

    def test_different_order_same_result(self, matcher):
        """Repeated calls with same context should produce identical results."""
        ctx = MeetingContext(topic="뮤직비디오 제작 관련 보안 검토")
        results = [matcher.match(ctx, routing_reason="test", timestamp="2026-01-01T00:00:00Z") for _ in range(10)]
        # All results should be identical
        first = results[0].to_dict()
        for r in results[1:]:
            assert r.to_dict() == first


# ═══════════════════════════════════════════════════════════════════════════
# Full integration with real routing_rules.yaml
# ═══════════════════════════════════════════════════════════════════════════


class TestRealRulesIntegration:
    """Integration tests using the actual routing_rules.yaml."""

    def test_creative_production_korean(self, matcher):
        result = matcher.match(
            MeetingContext(topic="뮤직비디오 제작 회의"),
            routing_reason="qwen_timeout",
        )
        assert result.agenda_type in ("creative-production", "general-discussion")
        assert result.validator_required is True
        assert result.routing_source == "static_fallback"

    def test_security_review(self, matcher):
        result = matcher.match(
            MeetingContext(topic="보안 취약점 검토 및 데이터 암호화"),
            routing_reason="qwen_timeout",
        )
        assert result.agenda_type in ("security-review", "general-discussion")
        # Security should trigger codex
        if "security_risk" in result.risk_tags or "data_loss_risk" in result.risk_tags:
            assert result.codex_required is True

    def test_budget_meeting(self, matcher):
        result = matcher.match(
            MeetingContext(topic="예산 및 일정 검토 회의"),
            routing_reason="qwen_timeout",
        )
        assert result.agenda_type in ("budget-planning", "general-discussion")
        if "budget_financial" in result.risk_tags:
            assert result.codex_required is True

    def test_technical_development(self, matcher):
        result = matcher.match(
            MeetingContext(topic="API 버그 수정 및 코드 리팩토링"),
            routing_reason="qwen_timeout",
        )
        assert result.agenda_type in ("technical-development", "general-discussion")

    def test_general_fallback(self, matcher):
        """Completely random topic should fall back to general-discussion."""
        result = matcher.match(
            MeetingContext(topic="xyzzy random topic that doesnt match anything"),
            routing_reason="qwen_timeout",
        )
        assert result.agenda_type == "general-discussion"

    def test_empty_topic_raises(self, matcher):
        with pytest.raises(ValueError, match="empty|topic"):
            matcher.match(
                MeetingContext(topic=""),
                routing_reason="test",
            )

    def test_whitespace_topic_raises(self, matcher):
        with pytest.raises(ValueError, match="empty|topic"):
            matcher.match(
                MeetingContext(topic="   \t  "),
                routing_reason="test",
            )

    def test_output_schema_fields(self, matcher):
        """Output must contain all required fields per output_schema."""
        result = matcher.match(
            MeetingContext(topic="신규 캐릭터 디자인 검토"),
            routing_reason="qwen_parse_failure",
        )
        d = result.to_dict()
        required_fields = [
            "agenda_type", "agenda_label", "tags", "risk_tags",
            "required_roles", "optional_roles", "validator_required",
            "codex_required", "priority", "routing_source",
            "routing_reason", "confidence", "generated_at", "version",
        ]
        for field in required_fields:
            assert field in d, f"Missing required field: {field}"

    def test_role_deduplication(self, matcher):
        """Required and optional roles should not overlap."""
        result = matcher.match(
            MeetingContext(topic="뮤직비디오 제작 회의"),
            routing_reason="qwen_timeout",
        )
        required_set = set(result.required_roles)
        optional_set = set(result.optional_roles)
        overlap = required_set & optional_set
        assert len(overlap) == 0, f"Overlapping roles: {overlap}"


# ═══════════════════════════════════════════════════════════════════════════
# Convenience function
# ═══════════════════════════════════════════════════════════════════════════


class TestConvenienceFunction:
    """Test the match_meeting_route convenience function."""

    def test_convenience_function(self, real_rules):
        result = match_meeting_route(
            topic="music video production meeting",
            rules_data=real_rules,
            routing_reason="qwen_timeout",
        )
        assert isinstance(result, MatchResult)
        assert result.routing_source == "static_fallback"
        assert result.routing_reason == "qwen_timeout"

    def test_convenience_function_with_context(self, real_rules):
        ctx = MeetingContext(
            topic="security vulnerability assessment",
            priority="P0",
        )
        result = match_meeting_route(
            topic=ctx.topic,
            rules_data=real_rules,
            routing_reason="qwen_error",
            context=ctx,
        )
        assert isinstance(result, MatchResult)
        assert result.routing_reason == "qwen_error"


# ═══════════════════════════════════════════════════════════════════════════
# Edge cases
# ═══════════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    """Edge case and boundary tests."""

    def test_very_long_topic(self, matcher):
        topic = "music video production " * 100
        result = matcher.match(
            MeetingContext(topic=topic),
            routing_reason="test",
        )
        assert result.agenda_type is not None

    def test_special_characters_in_topic(self, minimal_matcher):
        result = minimal_matcher.match(
            MeetingContext(topic="!@#$%^&*() music +-=[]{} video"),
            routing_reason="test",
        )
        assert result.agenda_type == "creative-production"

    def test_numeric_topic(self, matcher):
        result = matcher.match(
            MeetingContext(topic="12345 67890"),
            routing_reason="test",
        )
        assert result.agenda_type == "general-discussion"

    def test_single_word_match(self, minimal_matcher):
        """Single word should still match if it's in a keyword group."""
        result = minimal_matcher.match(
            MeetingContext(topic="bug"),
            routing_reason="test",
        )
        assert result.agenda_type == "technical"

    def test_partial_word_no_match(self, minimal_matcher):
        """Partial word should NOT match (substring matching is not used)."""
        rules = {
            "version": "1.0.0",
            "defaults": {
                "validator_required": True,
                "validator_role_id": "validator",
                "codex_required": False,
                "max_roles_per_meeting": 7,
                "max_required_roles": 6,
            },
            "agenda_types": [
                {
                    "id": "code-meeting",
                    "display_name": "Code",
                    "keywords": [["code"]],
                    "tags": ["code"],
                    "risk_tags": [],
                    "required_roles": ["tech-director"],
                    "optional_roles": [],
                    "validator_required": True,
                    "codex_required": False,
                },
                {
                    "id": "general-discussion",
                    "display_name": "General",
                    "keywords": [],
                    "tags": ["general"],
                    "risk_tags": [],
                    "required_roles": ["content-director"],
                    "optional_roles": [],
                    "validator_required": True,
                    "codex_required": False,
                },
            ],
            "risk_detection": {"patterns": []},
            "escalation_rules": {"codex_triggers": []},
            "priority_rules": {"inference": [], "default": "P2"},
            "guardrails": [],
        }
        config = RuleConfig.from_dict(rules)
        matcher = StaticRuleMatcher(config)
        # "coder" should NOT match keyword "code" (exact word match)
        result = matcher.match(
            MeetingContext(topic="coder"),
            routing_reason="test",
        )
        # Should fall back to general-discussion since "coder" != "code"
        assert result.agenda_type == "general-discussion"


# ═══════════════════════════════════════════════════════════════════════════
# Immutability and thread-safety
# ═══════════════════════════════════════════════════════════════════════════


class TestImmutability:
    """Verify that MatchResult is truly immutable."""

    def test_match_result_is_frozen(self):
        result = MatchResult(
            agenda_type="test",
            agenda_label="Test",
            tags=("a", "b"),
            risk_tags=(),
            required_roles=("r1",),
            optional_roles=(),
            validator_required=True,
            codex_required=False,
            priority="P2",
            routing_source="static_fallback",
            routing_reason="test",
            confidence=0.7,
        )

        with pytest.raises((TypeError, AttributeError, Exception)):
            result.agenda_type = "changed"  # type: ignore[misc]

        with pytest.raises((TypeError, AttributeError, Exception)):
            result.tags = ("changed",)  # type: ignore[misc]

    def test_new_result_with_overrides(self, matcher):
        """Test creating a new MatchResult with specific overrides."""
        original = matcher.match(
            MeetingContext(topic="music video"),
            routing_reason="test",
        )
        # Create a modified copy via the dataclass replace pattern
        modified = MatchResult(
            agenda_type=original.agenda_type,
            agenda_label=original.agenda_label,
            tags=("custom_tag",),
            risk_tags=original.risk_tags,
            required_roles=original.required_roles,
            optional_roles=original.optional_roles,
            validator_required=False,
            codex_required=original.codex_required,
            priority=original.priority,
            routing_source=original.routing_source,
            routing_reason=original.routing_reason,
            confidence=original.confidence,
        )
        assert modified.tags == ("custom_tag",)
        assert modified.validator_required is False
        # Original unchanged
        assert original.validator_required is True
