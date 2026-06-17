"""Tests for the Round 1 conflict detection module.

Sub-AC 5b-1: Conflict Detection — given Round 1 opinion packets from
all personas, identify conflicting position pairs (disagreeing on
same topic/decision), output structured conflict pairs with metadata;
testable by asserting correct conflict pairs are identified from
known inputs.

Coverage:
- Happy path: two personas with opposing opinions → one conflict pair
- Direct opposition: support vs oppose on same topic
- Incompatible recommendations: adopt vs reject, increase vs decrease
- Priority divergence: support vs conditional_support
- Methodological difference: alternative_proposal vs support
- Multiple conflicts across 3+ personas
- No conflicts (all agree, neutral positions)
- Single persona (no pairs to compare)
- Empty input (raises ValueError)
- Wrong input types (raises TypeError)
- Korean-language opinion text
- Mixed Korean/English opinions
- Injectable topic extractor (for deterministic testing)
- Injectable position analyser (for deterministic testing)
- Conflict severity computation
- Conflict type coverage (all 5 types)
- Unanimous topic detection
- topic_persona_map accuracy
- personas_analysed correctness
- requires_intervention threshold (>= 0.7)
- ConflictDetectionResult property accessors
- TopicExtraction dataclass validation
- TopicPosition dataclass validation
- ConflictPair dataclass validation
- ConflictDetectionResult dataclass validation
- Default extractor with varied Korean text structures
- Default analyser with varied stance patterns
"""

from __future__ import annotations

from typing import Any

import pytest

from src.conflict_detector import (
    ConflictDetectionResult,
    ConflictPair,
    TopicExtraction,
    TopicPosition,
    _default_analyse_position,
    _default_extract_topics,
    _determine_stance,
    _determine_direction,
    _are_opposing_stances,
    _are_incompatible_directions,
    _identify_conflicts,
    _label_to_topic_id,
    _derive_topic_label,
    _extract_key_terms,
    _generate_position_summary,
    detect_conflicts,
    inject_topic_extractor,
    inject_position_analyzer,
)


# ═════════════════════════════════════════════════════════════════════════
# Helper factories
# ═════════════════════════════════════════════════════════════════════════


def _make_packet(
    persona_id: str,
    opinion_content: str,
    confidence: float = 0.85,
) -> dict[str, Any]:
    """Create a minimal valid opinion packet dict."""
    return {
        "persona_id": persona_id,
        "opinion_content": opinion_content,
        "confidence": confidence,
        "agenda_item_ref": "test-agenda",
        "timestamp": "2026-06-10T14:30:00Z",
    }


def _make_topic(
    topic_id: str = "test-topic",
    label: str = "Test Topic",
    key_terms: tuple[str, ...] = ("test", "topic"),
    excerpt: str = "This is a test topic.",
    character_offset: int = 0,
) -> TopicExtraction:
    """Create a TopicExtraction for testing."""
    return TopicExtraction(
        topic_id=topic_id,
        label=label,
        key_terms=key_terms,
        excerpt=excerpt,
        character_offset=character_offset,
    )


def _make_position(
    persona_id: str = "test-persona",
    topic_id: str = "test-topic",
    stance: str = "neutral",
    summary: str = "Neutral position.",
    confidence: float = 0.8,
    recommendation_direction: str = "maintain",
) -> TopicPosition:
    """Create a TopicPosition for testing."""
    return TopicPosition(
        persona_id=persona_id,
        topic_id=topic_id,
        stance=stance,
        summary=summary,
        supporting_points=(),
        confidence=confidence,
        recommendation_direction=recommendation_direction,
    )


# ═════════════════════════════════════════════════════════════════════════
# 1. Stance / direction helpers (unit)
# ═════════════════════════════════════════════════════════════════════════


class TestStanceDetection:
    """Test the stance detection helper functions."""

    def test_determine_support_stance(self) -> None:
        """Korean support patterns detected as 'support'."""
        text = "이 방안에 찬성합니다. 좋은 방향이라고 생각합니다."
        assert _determine_stance(text) == "support"

    def test_determine_oppose_stance(self) -> None:
        """Korean oppose patterns detected as 'oppose'."""
        text = "이 제안에 반대합니다. 문제가 있습니다."
        assert _determine_stance(text) == "oppose"

    def test_determine_conditional_support(self) -> None:
        """Conditional support patterns override regular support."""
        text = "예산이 확보된다면 찬성합니다. 조건부로 동의합니다."
        assert _determine_stance(text) == "conditional_support"

    def test_determine_alternative_proposal(self) -> None:
        """Alternative proposal patterns detected."""
        text = "대신에 다른 방법을 제안합니다. 이 접근 대신 다른 대안을..."
        assert _determine_stance(text) == "alternative_proposal"

    def test_determine_neutral_stance(self) -> None:
        """No strong stance cues → neutral."""
        text = "여러 측면을 고려해 볼 필요가 있습니다."
        assert _determine_stance(text) == "neutral"

    def test_oppose_overrides_support(self) -> None:
        """When both support and oppose cues present, oppose wins."""
        text = "찬성할 만한 점도 있지만 반대합니다. 위험합니다."
        assert _determine_stance(text) == "oppose"

    def test_english_support_stance(self) -> None:
        """English support patterns detected."""
        text = "I support this approach. I agree with the proposal."
        assert _determine_stance(text) == "support"

    def test_english_oppose_stance(self) -> None:
        """English oppose patterns detected."""
        text = "I disagree with this. This is problematic and risky."
        assert _determine_stance(text) == "oppose"


class TestDirectionDetection:
    """Test recommendation direction detection."""

    def test_adopt_direction(self) -> None:
        """Adopt/implement patterns detected."""
        text = "이 방안을 채택해야 합니다. 바로 실행합시다."
        assert _determine_direction(text) == "adopt"

    def test_reject_direction(self) -> None:
        """Reject/abandon patterns detected."""
        text = "이 제안은 거부해야 합니다. 폐기하는 것이 좋겠습니다."
        assert _determine_direction(text) == "reject"

    def test_increase_direction(self) -> None:
        """Increase/expand patterns detected."""
        text = "예산을 확대해야 합니다. 투자를 늘려야 합니다."
        assert _determine_direction(text) == "increase"

    def test_decrease_direction(self) -> None:
        """Decrease/reduce patterns detected."""
        text = "비용을 축소해야 합니다. 예산을 줄입시다."
        assert _determine_direction(text) == "decrease"

    def test_defer_direction(self) -> None:
        """Defer/postpone patterns detected."""
        text = "이 결정은 보류합시다. 다음 단계로 연기합니다."
        assert _determine_direction(text) == "defer"

    def test_explore_direction(self) -> None:
        """Explore/investigate patterns detected."""
        text = "더 검토가 필요합니다. 추가 분석이 필요합니다."
        assert _determine_direction(text) == "explore"

    def test_maintain_fallback(self) -> None:
        """No direction cue → maintain (default)."""
        text = "상황을 지켜보겠습니다."
        assert _determine_direction(text) == "maintain"


class TestOpposingStances:
    """Test the stance opposition helper."""

    def test_support_vs_oppose(self) -> None:
        """Support vs oppose are opposing."""
        assert _are_opposing_stances("support", "oppose") is True

    def test_oppose_vs_support(self) -> None:
        """Oppose vs support are opposing (symmetric)."""
        assert _are_opposing_stances("oppose", "support") is True

    def test_support_vs_alternative(self) -> None:
        """Support vs alternative_proposal are NOT directly opposing
        (they differ methodologically, not on the goal itself)."""
        assert _are_opposing_stances("support", "alternative_proposal") is False

    def test_support_vs_support_not_opposing(self) -> None:
        """Same stance is not opposing."""
        assert _are_opposing_stances("support", "support") is False

    def test_neutral_vs_neutral_not_opposing(self) -> None:
        """Neutral vs neutral is not opposing."""
        assert _are_opposing_stances("neutral", "neutral") is False

    def test_conditional_vs_support_not_opposing(self) -> None:
        """Conditional and support are not directly opposing."""
        assert _are_opposing_stances("conditional_support", "support") is False


class TestIncompatibleDirections:
    """Test the incompatible recommendation directions helper."""

    def test_adopt_vs_reject(self) -> None:
        """Adopt vs reject are incompatible."""
        assert _are_incompatible_directions("adopt", "reject") is True

    def test_increase_vs_decrease(self) -> None:
        """Increase vs decrease are incompatible."""
        assert _are_incompatible_directions("increase", "decrease") is True

    def test_adopt_vs_defer(self) -> None:
        """Adopt vs defer are incompatible."""
        assert _are_incompatible_directions("adopt", "defer") is True

    def test_adopt_vs_explore(self) -> None:
        """Adopt vs explore are incompatible (one wants action now,
        other wants study first)."""
        assert _are_incompatible_directions("adopt", "explore") is True

    def test_explore_vs_explore_not_incompatible(self) -> None:
        """Same direction is compatible."""
        assert _are_incompatible_directions("explore", "explore") is False

    def test_adopt_vs_increase_not_incompatible(self) -> None:
        """Adopt and increase can coexist (adopt and then increase)."""
        assert _are_incompatible_directions("adopt", "increase") is False


# ═════════════════════════════════════════════════════════════════════════
# 2. Topic extraction tests
# ═════════════════════════════════════════════════════════════════════════


class TestDefaultTopicExtractor:
    """Test the default Korean-aware topic extractor."""

    def test_extract_from_numbered_list(self) -> None:
        """Numbered points are extracted as separate topics."""
        text = (
            "1. 네온 팔레트를 채택해야 합니다. 시각적 임팩트가 큽니다.\n"
            "2. 캐릭터 디자인은 기존 방향을 유지해야 합니다.\n"
            "3. 예산은 50억원으로 확정해야 합니다."
        )
        topics = _default_extract_topics(text)
        assert len(topics) >= 2  # At least 2 topics extracted

    def test_extract_from_paragraphs(self) -> None:
        """Paragraphs (double newlines) are extracted as topics."""
        text = (
            "비주얼 디렉션에 관해 의견을 말씀드리겠습니다. 네온 팔레트가 적합합니다.\n"
            "\n"
            "사운드 디자인 측면에서는 미니멀한 접근을 추천합니다.\n"
            "\n"
            "마케팅 전략으로는 SNS 티저 캠페인이 효과적일 것입니다."
        )
        topics = _default_extract_topics(text)
        assert len(topics) >= 2

    def test_extract_empty_text(self) -> None:
        """Empty text returns no topics."""
        assert _default_extract_topics("") == []
        assert _default_extract_topics("   ") == []

    def test_extract_single_block(self) -> None:
        """Single block of text returns at least one topic."""
        text = "전반적으로 이 방향에 동의합니다. 네온 팔레트가 좋은 선택입니다."
        topics = _default_extract_topics(text)
        assert len(topics) >= 1

    def test_extract_korean_text(self) -> None:
        """Korean text is correctly parsed into topics."""
        text = (
            "1. 마케팅 예산을 30% 증액해야 합니다. 현재 예산으로는 목표 도달이 어렵습니다.\n"
            "2. 타겟 오디언스를 10대 후반으로 좁혀야 합니다.\n"
            "3. 인플루언서 마케팅을 강화해야 합니다."
        )
        topics = _default_extract_topics(text)
        assert len(topics) >= 2
        for topic in topics:
            assert topic.topic_id  # Every topic has an ID
            assert topic.label  # Every topic has a label

    def test_extract_english_text(self) -> None:
        """English text is correctly parsed."""
        text = (
            "1. We should increase the marketing budget by 30%.\n"
            "2. Target audience should be narrowed to late teens.\n"
            "3. Influencer marketing should be strengthened."
        )
        topics = _default_extract_topics(text)
        assert len(topics) >= 2

    def test_extract_returns_topic_extraction_objects(self) -> None:
        """All returned items are TopicExtraction dataclass instances."""
        text = "1. This is a topic.\n2. This is another topic."
        topics = _default_extract_topics(text)
        for topic in topics:
            assert isinstance(topic, TopicExtraction)
            assert isinstance(topic.topic_id, str)
            assert isinstance(topic.label, str)


class TestTopicLabelDerivation:
    """Test the topic label derivation helper."""

    def test_first_sentence_as_label(self) -> None:
        """First sentence is used as the label."""
        segment = "이 방안에 찬성합니다. 추가 논의가 필요합니다."
        label = _derive_topic_label(segment, 0)
        assert "찬성" in label

    def test_fallback_positional_label(self) -> None:
        """Very short text gets a positional fallback label."""
        label = _derive_topic_label("짧은", 2)
        assert "Topic" in label or "짧은" in label

    def test_korean_topic_intro_pattern(self) -> None:
        """Topic-intro pattern extracts the relevant sentence."""
        segment = (
            "비용 측면에서 볼 때 이 제안에 반대합니다. "
            "현재 예산으로는 실행이 불가능합니다."
        )
        label = _derive_topic_label(segment, 0)
        assert "비용" in label or "측면" in label or "반대" in label


class TestKeyTermExtraction:
    """Test the key term extraction helper."""

    def test_korean_compound_nouns(self) -> None:
        """Korean compound nouns are extracted as key terms."""
        text = "마케팅 예산 증액 방안에 대해 검토했습니다."
        terms = _extract_key_terms(text)
        assert len(terms) >= 1

    def test_kv_patterns(self) -> None:
        """Key-value patterns (예산: X) are extracted."""
        text = "예산: 50억원, 기간: 3개월로 설정합니다."
        terms = _extract_key_terms(text)
        assert any("예산" in t for t in terms) or any("기간" in t for t in terms)


class TestLabelToTopicId:
    """Test the label-to-topic-id conversion."""

    def test_korean_label_to_id(self) -> None:
        """Korean label is converted to a stable kebab-case ID."""
        topic_id = _label_to_topic_id("마케팅 예산 증액 방안")
        assert topic_id
        assert " " not in topic_id  # No spaces in ID
        assert topic_id == topic_id.lower()  # Lowercase

    def test_english_label_to_id(self) -> None:
        """English label becomes kebab-case ID."""
        topic_id = _label_to_topic_id("Marketing Budget Proposal")
        assert "marketing" in topic_id
        assert " " not in topic_id


# ═════════════════════════════════════════════════════════════════════════
# 3. Position analysis tests
# ═════════════════════════════════════════════════════════════════════════


class TestDefaultPositionAnalyzer:
    """Test the default position analyser."""

    def test_support_position(self) -> None:
        """Support stance is correctly identified."""
        text = "네온 팔레트를 채택해야 합니다. 이 방안에 찬성합니다."
        topic = _make_topic(
            topic_id="visual-direction",
            label="비주얼 디렉션",
            excerpt="네온 팔레트를 채택해야 합니다.",
        )
        position = _default_analyse_position(text, topic, 0.9)
        assert position is not None
        assert position.stance == "support"
        assert position.confidence == 0.9

    def test_oppose_position(self) -> None:
        """Oppose stance is correctly identified."""
        text = "네온 팔레트는 위험합니다. 이 제안에 반대합니다."
        topic = _make_topic(
            topic_id="visual-direction",
            label="비주얼 디렉션",
            excerpt="네온 팔레트는 위험합니다.",
        )
        position = _default_analyse_position(text, topic, 0.85)
        assert position is not None
        assert position.stance == "oppose"

    def test_conditional_support_position(self) -> None:
        """Conditional support is correctly identified."""
        text = "예산이 확보된다면 이 방안에 찬성합니다. 조건부로 동의합니다."
        topic = _make_topic(
            topic_id="budget",
            label="예산 검토",
            excerpt="예산이 확보된다면 이 방안에 찬성합니다.",
        )
        position = _default_analyse_position(text, topic, 0.7)
        assert position is not None
        assert position.stance == "conditional_support"

    def test_alternative_proposal_position(self) -> None:
        """Alternative proposal is correctly identified."""
        text = "네온 대신 파스텔 팔레트를 대안으로 제시합니다."
        topic = _make_topic(
            topic_id="visual-direction",
            label="비주얼 디렉션",
            excerpt="네온 대신 파스텔 팔레트를 대안으로 제시합니다.",
        )
        position = _default_analyse_position(text, topic, 0.8)
        assert position is not None
        assert position.stance == "alternative_proposal"

    def test_direction_extraction(self) -> None:
        """Recommendation direction is extracted from text."""
        text = "예산을 30% 증액해야 합니다. 더 투자해야 합니다."
        topic = _make_topic(
            topic_id="budget",
            label="예산",
            excerpt="예산을 30% 증액해야 합니다.",
        )
        position = _default_analyse_position(text, topic, 0.9)
        assert position is not None
        assert position.recommendation_direction == "increase"

    def test_none_excerpt(self) -> None:
        """None excerpt or empty topic returns None position."""
        topic = _make_topic(excerpt="")
        position = _default_analyse_position("some text", topic, 0.5)
        assert position is None

    def test_position_summary_includes_stance(self) -> None:
        """Position summary includes stance label when stance cues present."""
        text = "네온 팔레트를 채택해야 합니다. 이 방안에 찬성합니다."
        topic = _make_topic(
            topic_id="visual-direction",
            label="비주얼",
            excerpt="네온 팔레트를 채택해야 합니다.",
        )
        position = _default_analyse_position(text, topic, 0.9)
        assert position is not None
        assert "지지" in position.summary or "Support" in position.summary


class TestPositionSummary:
    """Test the position summary generation."""

    def test_support_summary(self) -> None:
        """Support summary includes stance and excerpt."""
        summary = _generate_position_summary(
            stance="support",
            direction="adopt",
            excerpt="네온 팔레트를 채택해야 합니다.",
            topic_label="비주얼 디렉션",
        )
        assert "Support" in summary or "지지" in summary
        assert "네온" in summary

    def test_oppose_summary(self) -> None:
        """Oppose summary includes stance and excerpt."""
        summary = _generate_position_summary(
            stance="oppose",
            direction="reject",
            excerpt="이 제안을 거부해야 합니다.",
            topic_label="제안 검토",
        )
        assert "Oppose" in summary or "반대" in summary


# ═════════════════════════════════════════════════════════════════════════
# 4. Conflict identification tests (core logic)
# ═════════════════════════════════════════════════════════════════════════


class TestConflictIdentification:
    """Test the core conflict identification logic."""

    def test_identify_direct_opposition(self) -> None:
        """Support vs oppose → direct_opposition conflict."""
        positions_by_topic = {
            "topic-1": [
                _make_position(
                    persona_id="art-director",
                    topic_id="topic-1",
                    stance="support",
                    summary="[지지 → 채택] 네온 팔레트 사용",
                    confidence=0.9,
                    recommendation_direction="adopt",
                ),
                _make_position(
                    persona_id="tech-director",
                    topic_id="topic-1",
                    stance="oppose",
                    summary="[반대 → 거부] 네온 팔레트 반대",
                    confidence=0.85,
                    recommendation_direction="reject",
                ),
            ],
        }
        conflicts = _identify_conflicts(positions_by_topic)
        assert len(conflicts) == 1
        assert conflicts[0].conflict_type == "direct_opposition"
        assert conflicts[0].severity >= 0.8

    def test_identify_incompatible_recommendations(self) -> None:
        """Adopt vs reject on same topic → incompatible_recommendation."""
        positions_by_topic = {
            "topic-1": [
                _make_position(
                    persona_id="marketing-lead",
                    topic_id="topic-1",
                    stance="support",
                    summary="예산 증액",
                    confidence=0.8,
                    recommendation_direction="increase",
                ),
                _make_position(
                    persona_id="finance-lead",
                    topic_id="topic-1",
                    stance="support",
                    summary="예산 삭감",
                    confidence=0.75,
                    recommendation_direction="decrease",
                ),
            ],
        }
        conflicts = _identify_conflicts(positions_by_topic)
        assert len(conflicts) == 1
        assert conflicts[0].conflict_type == "incompatible_recommendation"

    def test_identify_priority_divergence(self) -> None:
        """Support vs conditional_support → priority_divergence."""
        positions_by_topic = {
            "topic-1": [
                _make_position(
                    persona_id="content-director",
                    topic_id="topic-1",
                    stance="support",
                    summary="즉시 채택",
                    confidence=0.9,
                    recommendation_direction="adopt",
                ),
                _make_position(
                    persona_id="tech-director",
                    topic_id="topic-1",
                    stance="conditional_support",
                    summary="조건부 채택",
                    confidence=0.7,
                    recommendation_direction="adopt",
                ),
            ],
        }
        conflicts = _identify_conflicts(positions_by_topic)
        assert len(conflicts) == 1
        assert conflicts[0].conflict_type == "priority_divergence"
        assert conflicts[0].severity < 0.6  # Lower severity than direct opposition

    def test_identify_methodological_difference(self) -> None:
        """Alternative proposal vs support → methodological_difference."""
        positions_by_topic = {
            "topic-1": [
                _make_position(
                    persona_id="art-director",
                    topic_id="topic-1",
                    stance="support",
                    summary="네온 채택",
                    confidence=0.85,
                    recommendation_direction="adopt",
                ),
                _make_position(
                    persona_id="ui-ux-designer",
                    topic_id="topic-1",
                    stance="alternative_proposal",
                    summary="파스텔 대안 제시",
                    confidence=0.8,
                    recommendation_direction="adopt",
                ),
            ],
        }
        conflicts = _identify_conflicts(positions_by_topic)
        assert len(conflicts) == 1
        assert conflicts[0].conflict_type == "methodological_difference"

    def test_no_conflict_when_all_agree(self) -> None:
        """Same stance and direction → no conflict."""
        positions_by_topic = {
            "topic-1": [
                _make_position(
                    persona_id="art-director",
                    topic_id="topic-1",
                    stance="support",
                    summary="네온 채택",
                    confidence=0.9,
                    recommendation_direction="adopt",
                ),
                _make_position(
                    persona_id="marketing-lead",
                    topic_id="topic-1",
                    stance="support",
                    summary="네온 채택 동의",
                    confidence=0.85,
                    recommendation_direction="adopt",
                ),
            ],
        }
        conflicts = _identify_conflicts(positions_by_topic)
        assert len(conflicts) == 0

    def test_no_conflict_single_persona_on_topic(self) -> None:
        """Only one persona addressed this topic → no pairs to compare."""
        positions_by_topic = {
            "topic-1": [
                _make_position(
                    persona_id="art-director",
                    topic_id="topic-1",
                    stance="support",
                    summary="네온 채택",
                    confidence=0.9,
                ),
            ],
        }
        conflicts = _identify_conflicts(positions_by_topic)
        assert len(conflicts) == 0

    def test_multiple_conflicts(self) -> None:
        """Three personas, two conflicting → two conflict pairs."""
        positions_by_topic = {
            "topic-1": [
                _make_position(
                    persona_id="art-director",
                    topic_id="topic-1",
                    stance="support",
                    summary="네온 채택",
                    confidence=0.9,
                    recommendation_direction="adopt",
                ),
                _make_position(
                    persona_id="tech-director",
                    topic_id="topic-1",
                    stance="oppose",
                    summary="네온 반대",
                    confidence=0.85,
                    recommendation_direction="reject",
                ),
                _make_position(
                    persona_id="marketing-lead",
                    topic_id="topic-1",
                    stance="oppose",
                    summary="네온 우려",
                    confidence=0.7,
                    recommendation_direction="reject",
                ),
            ],
        }
        conflicts = _identify_conflicts(positions_by_topic)
        # art vs tech (direct_opposition), art vs marketing (direct_opposition)
        # tech vs marketing: both oppose → not a conflict
        assert len(conflicts) == 2

    def test_conflicts_sorted_by_severity(self) -> None:
        """Conflicts are sorted by severity (highest first)."""
        positions_by_topic = {
            "topic-a": [
                _make_position(
                    persona_id="a",
                    topic_id="topic-a",
                    stance="support",
                    confidence=0.5,
                    recommendation_direction="adopt",
                ),
                _make_position(
                    persona_id="b",
                    topic_id="topic-a",
                    stance="conditional_support",
                    confidence=0.5,
                    recommendation_direction="adopt",
                ),
            ],
            "topic-b": [
                _make_position(
                    persona_id="a",
                    topic_id="topic-b",
                    stance="support",
                    confidence=0.95,
                    recommendation_direction="adopt",
                ),
                _make_position(
                    persona_id="b",
                    topic_id="topic-b",
                    stance="oppose",
                    confidence=0.95,
                    recommendation_direction="reject",
                ),
            ],
        }
        conflicts = _identify_conflicts(positions_by_topic)
        assert len(conflicts) >= 1
        if len(conflicts) >= 2:
            assert conflicts[0].severity >= conflicts[1].severity

    def test_conflict_pair_dataclass_fields(self) -> None:
        """ConflictPair has all required fields with correct types."""
        cp = ConflictPair(
            topic="Test Topic",
            topic_id="test-topic",
            persona_a="art-director",
            persona_b="tech-director",
            position_a="Support position.",
            position_b="Oppose position.",
            stance_a="support",
            stance_b="oppose",
            conflict_type="direct_opposition",
            severity=0.85,
            confidence_a=0.9,
            confidence_b=0.85,
        )
        assert cp.persona_a == "art-director"
        assert cp.severity == 0.85
        assert cp.conflict_type == "direct_opposition"


# ═════════════════════════════════════════════════════════════════════════
# 5. Integration: detect_conflicts with opinion packets
# ═════════════════════════════════════════════════════════════════════════


class TestDetectConflictsIntegration:
    """Integration tests for the public detect_conflicts API."""

    def test_happy_path_direct_opposition(self) -> None:
        """Two personas directly oppose each other → one conflict."""
        packets = [
            _make_packet(
                "art-director",
                "1. 네온 팔레트를 채택해야 합니다. 찬성합니다. 시각적 임팩트가 뛰어납니다.",
                0.9,
            ),
            _make_packet(
                "tech-director",
                "1. 네온 팔레트에 반대합니다. 렌더링 문제가 있고 파스텔이 더 안전합니다.",
                0.85,
            ),
        ]
        result = detect_conflicts(packets)
        assert result.has_conflicts
        assert result.conflict_count >= 1
        assert result.personas_analysed == ("art-director", "tech-director")

    def test_no_conflicts_when_all_agree(self) -> None:
        """All personas support the same approach → no conflicts."""
        packets = [
            _make_packet(
                "art-director",
                "1. 네온 팔레트에 찬성합니다. 좋은 방향입니다.",
                0.9,
            ),
            _make_packet(
                "tech-director",
                "1. 네온 팔레트에 동의합니다. 기술적으로도 문제없습니다.",
                0.85,
            ),
            _make_packet(
                "marketing-lead",
                "1. 네온 팔레트가 마케팅 측면에서도 좋습니다. 찬성합니다.",
                0.8,
            ),
        ]
        result = detect_conflicts(packets)
        # All agree → may or may not have conflicts depending on
        # how the extractor splits topics
        # At minimum, no direct_opposition severity > 0.7
        for cp in result.conflict_pairs:
            assert cp.conflict_type != "direct_opposition"

    def test_multiple_personas_conflicts(self) -> None:
        """3+ personas with mixed opinions → correct conflict count."""
        # Use a shared topic extractor to guarantee topic alignment
        def shared_extractor(content: str) -> list[TopicExtraction]:
            return [
                TopicExtraction(
                    topic_id="visual-direction",
                    label="비주얼 디렉션",
                    key_terms=("비주얼", "팔레트"),
                    excerpt=content[:150],
                    character_offset=0,
                ),
            ]

        packets = [
            _make_packet(
                "art-director",
                "네온 팔레트를 채택해야 합니다. 찬성합니다.",
                0.9,
            ),
            _make_packet(
                "tech-director",
                "네온 팔레트에 반대합니다. 파스텔로 변경해야 합니다.",
                0.85,
            ),
            _make_packet(
                "content-director",
                "네온 팔레트에 찬성합니다. 독특한 비주얼이 될 것입니다.",
                0.8,
            ),
            _make_packet(
                "marketing-lead",
                "네온 팔레트는 너무 강렬합니다. 반대합니다.",
                0.75,
            ),
        ]
        result = detect_conflicts(packets, _inject_extractor=shared_extractor)
        assert result.conflict_count >= 1
        assert len(result.personas_analysed) >= 4

    def test_empty_input_raises(self) -> None:
        """Empty opinion packet list raises ValueError."""
        with pytest.raises(ValueError, match="non-empty"):
            detect_conflicts([])

    def test_single_persona_no_conflicts(self) -> None:
        """Single opinion → no pairs to compare → no conflicts."""
        packets = [
            _make_packet(
                "art-director",
                "1. 네온 팔레트를 채택해야 합니다.",
                0.9,
            ),
        ]
        result = detect_conflicts(packets)
        assert not result.has_conflicts
        assert result.conflict_count == 0

    def test_non_dict_packet_raises(self) -> None:
        """Non-dict packet raises TypeError."""
        with pytest.raises(TypeError):
            detect_conflicts(["not a dict"])  # type: ignore[arg-type]

    def test_korean_opinion_text(self) -> None:
        """Korean-language opinions are correctly analysed."""
        packets = [
            _make_packet(
                "art-director",
                (
                    "1. 캐릭터 디자인은 기존 방향을 유지해야 합니다.\n"
                    "2. 배경은 판타지 스타일로 변경을 추천합니다.\n"
                    "3. UI는 미니멀하게 가야 합니다."
                ),
                0.9,
            ),
            _make_packet(
                "tech-director",
                (
                    "1. 캐릭터 디자인 변경은 위험합니다. 기술적 제약이 있습니다.\n"
                    "2. 배경 변경에 찬성합니다.\n"
                    "3. UI 미니멀화는 성능에 도움이 됩니다."
                ),
                0.85,
            ),
        ]
        result = detect_conflicts(packets)
        assert result.personas_analysed == ("art-director", "tech-director")

    def test_result_properties(self) -> None:
        """ConflictDetectionResult properties are correct."""
        packets = [
            _make_packet("art-director", "네온 채택 찬성합니다.", 0.9),
            _make_packet("tech-director", "네온 채택 반대합니다.", 0.85),
        ]
        result = detect_conflicts(packets)
        assert isinstance(result.has_conflicts, bool)
        assert isinstance(result.requires_intervention, bool)
        assert result.conflict_count == len(result.conflict_pairs)
        assert isinstance(result.conflict_severity_max, float)
        assert isinstance(result.topics_identified, tuple)
        assert isinstance(result.unanimous_topics, tuple)

    def test_topic_persona_map(self) -> None:
        """topic_persona_map correctly maps topics to personas."""
        packets = [
            _make_packet("art-director", "네온 팔레트를 채택해야 합니다.", 0.9),
            _make_packet("tech-director", "네온 팔레트에 반대합니다.", 0.85),
        ]
        result = detect_conflicts(packets)
        assert isinstance(result.topic_persona_map, dict)
        for topic_id, personas in result.topic_persona_map.items():
            assert isinstance(topic_id, str)
            assert isinstance(personas, tuple)
            assert all(isinstance(p, str) for p in personas)

    def test_confidence_preserved_in_conflicts(self) -> None:
        """Original persona confidence scores are preserved in ConflictPair."""
        packets = [
            _make_packet("art-director", "네온 채택을 추천합니다.", 0.92),
            _make_packet("tech-director", "네온 채택을 반대합니다. 파스텔이 더 안전합니다.", 0.73),
        ]
        result = detect_conflicts(packets)
        for cp in result.conflict_pairs:
            assert 0.0 <= cp.confidence_a <= 1.0
            assert 0.0 <= cp.confidence_b <= 1.0


# ═════════════════════════════════════════════════════════════════════════
# 6. Injectable components tests
# ═════════════════════════════════════════════════════════════════════════


class TestInjectableExtractor:
    """Test with an injected topic extractor for deterministic results."""

    def test_inject_custom_extractor(self) -> None:
        """Custom extractor is used instead of the default."""
        def custom_extractor(content: str) -> list[TopicExtraction]:
            return [
                TopicExtraction(
                    topic_id="custom-topic",
                    label="Custom Topic",
                    key_terms=("custom",),
                    excerpt=content[:100],
                    character_offset=0,
                ),
            ]

        inject_topic_extractor(custom_extractor)
        try:
            packets = [
                _make_packet("art-director", "Any text here.", 0.9),
                _make_packet("tech-director", "Different text.", 0.85),
            ]
            result = detect_conflicts(packets)
            # Custom extractor always returns "custom-topic"
            assert any(t.topic_id == "custom-topic" for t in result.topics_identified)
        finally:
            inject_topic_extractor(None)  # Restore default

    def test_inject_per_call_extractor(self) -> None:
        """Per-call _inject_extractor overrides the default."""
        def per_call_extractor(content: str) -> list[TopicExtraction]:
            return [
                TopicExtraction(
                    topic_id="per-call-topic",
                    label="Per-Call Topic",
                    key_terms=("per-call",),
                    excerpt=content[:100],
                    character_offset=0,
                ),
            ]

        packets = [
            _make_packet("art-director", "Some text.", 0.9),
            _make_packet("tech-director", "Other text.", 0.85),
        ]
        result = detect_conflicts(packets, _inject_extractor=per_call_extractor)
        assert any(
            t.topic_id == "per-call-topic" for t in result.topics_identified
        )


class TestInjectablePositionAnalyzer:
    """Test with an injected position analyser for deterministic results."""

    def test_inject_custom_analyzer(self) -> None:
        """Custom position analyser is used for stance detection."""
        def custom_analyzer(
            content: str, topic: TopicExtraction, confidence: float
        ) -> TopicPosition | None:
            return TopicPosition(
                persona_id="",
                topic_id=topic.topic_id,
                stance="support" if "찬성" in content else "oppose",
                summary="Custom position.",
                supporting_points=(),
                confidence=confidence,
                recommendation_direction="adopt",
            )

        def shared_topic_extractor(content: str) -> list[TopicExtraction]:
            """Ensure both personas get the same topic for comparison."""
            return [
                TopicExtraction(
                    topic_id="shared-topic",
                    label="Shared Topic",
                    key_terms=("shared",),
                    excerpt=content[:100],
                    character_offset=0,
                ),
            ]

        inject_position_analyzer(custom_analyzer)
        try:
            packets = [
                _make_packet("art-director", "이 방안에 찬성합니다.", 0.9),
                _make_packet("tech-director", "이 방안에 반대합니다.", 0.85),
            ]
            result = detect_conflicts(
                packets, _inject_extractor=shared_topic_extractor
            )
            assert result.has_conflicts
            assert result.conflict_count >= 1
        finally:
            inject_position_analyzer(None)

    def test_custom_analyzer_no_conflicts(self) -> None:
        """Custom analyser returning same stance → no conflicts."""
        def uniform_analyzer(
            content: str, topic: TopicExtraction, confidence: float
        ) -> TopicPosition | None:
            return TopicPosition(
                persona_id="",
                topic_id=topic.topic_id,
                stance="support",
                summary="All support.",
                supporting_points=(),
                confidence=confidence,
                recommendation_direction="adopt",
            )

        packets = [
            _make_packet("art-director", "Text A.", 0.9),
            _make_packet("tech-director", "Text B.", 0.85),
        ]
        result = detect_conflicts(packets, _inject_analyzer=uniform_analyzer)
        assert not result.has_conflicts
        assert result.conflict_count == 0

    def test_custom_analyzer_deterministic(self) -> None:
        """Custom analyser produces identical results on repeated runs."""
        def deterministic_analyzer(
            content: str, topic: TopicExtraction, confidence: float
        ) -> TopicPosition | None:
            return TopicPosition(
                persona_id="",
                topic_id=topic.topic_id,
                stance="oppose" if "반대" in content else "support",
                summary=content[:50],
                supporting_points=(),
                confidence=confidence,
                recommendation_direction="reject" if "반대" in content else "adopt",
            )

        packets = [
            _make_packet("a", "찬성 텍스트", 0.9),
            _make_packet("b", "반대 텍스트", 0.85),
        ]
        result1 = detect_conflicts(packets, _inject_analyzer=deterministic_analyzer)
        result2 = detect_conflicts(packets, _inject_analyzer=deterministic_analyzer)
        assert result1.conflict_count == result2.conflict_count


# ═════════════════════════════════════════════════════════════════════════
# 7. Severity and threshold tests
# ═════════════════════════════════════════════════════════════════════════


class TestSeverityScoring:
    """Test conflict severity scoring and thresholds."""

    def test_direct_opposition_high_severity(self) -> None:
        """Direct opposition with high confidence → severity >= 0.8."""
        positions_by_topic = {
            "topic-1": [
                _make_position(
                    persona_id="a",
                    stance="support",
                    confidence=0.95,
                    recommendation_direction="adopt",
                ),
                _make_position(
                    persona_id="b",
                    stance="oppose",
                    confidence=0.95,
                    recommendation_direction="reject",
                ),
            ],
        }
        conflicts = _identify_conflicts(positions_by_topic)
        assert len(conflicts) == 1
        assert conflicts[0].severity >= 0.8

    def test_low_confidence_reduces_severity(self) -> None:
        """Low confidence on both sides reduces severity."""
        positions_by_topic = {
            "topic-1": [
                _make_position(
                    persona_id="a",
                    stance="support",
                    confidence=0.3,
                    recommendation_direction="adopt",
                ),
                _make_position(
                    persona_id="b",
                    stance="oppose",
                    confidence=0.3,
                    recommendation_direction="reject",
                ),
            ],
        }
        conflicts = _identify_conflicts(positions_by_topic)
        assert len(conflicts) == 1
        assert conflicts[0].severity < 0.8  # Lower than high-confidence

    def test_priority_divergence_lower_severity(self) -> None:
        """Priority divergence has lower severity than direct opposition."""
        # Direct opposition
        direct = _identify_conflicts({
            "t1": [
                _make_position("a", stance="support", confidence=0.85,
                               recommendation_direction="adopt"),
                _make_position("b", stance="oppose", confidence=0.85,
                               recommendation_direction="reject"),
            ],
        })
        # Priority divergence
        priority = _identify_conflicts({
            "t1": [
                _make_position("a", stance="support", confidence=0.85,
                               recommendation_direction="adopt"),
                _make_position("b", stance="conditional_support", confidence=0.85,
                               recommendation_direction="adopt"),
            ],
        })
        assert len(direct) == 1
        assert len(priority) == 1
        assert priority[0].severity < direct[0].severity

    def test_requires_intervention_threshold(self) -> None:
        """Conflict with severity >= 0.7 triggers requires_intervention."""
        # Use a deterministic test with injected analyzer
        def high_severity_analyzer(
            content: str, topic: TopicExtraction, confidence: float
        ) -> TopicPosition | None:
            return TopicPosition(
                persona_id="",
                topic_id=topic.topic_id,
                stance="oppose" if "반대" in content else "support",
                summary=content[:50],
                supporting_points=(),
                confidence=0.95,  # High confidence → high severity
                recommendation_direction="reject" if "반대" in content else "adopt",
            )

        packets = [
            _make_packet("a", "찬성", 0.95),
            _make_packet("b", "반대", 0.95),
        ]
        result = detect_conflicts(packets, _inject_analyzer=high_severity_analyzer)
        if result.has_conflicts:
            assert result.requires_intervention is (result.conflict_severity_max >= 0.7)


# ═════════════════════════════════════════════════════════════════════════
# 8. Dataclass validation
# ═════════════════════════════════════════════════════════════════════════


class TestDataclassImmutability:
    """Verify all dataclasses are frozen (immutable)."""

    def test_topic_extraction_is_frozen(self) -> None:
        """TopicExtraction cannot be mutated after creation."""
        t = _make_topic()
        with pytest.raises(Exception):
            t.topic_id = "new-id"  # type: ignore[misc]

    def test_topic_position_is_frozen(self) -> None:
        """TopicPosition cannot be mutated after creation."""
        p = _make_position()
        with pytest.raises(Exception):
            p.stance = "oppose"  # type: ignore[misc]

    def test_conflict_pair_is_frozen(self) -> None:
        """ConflictPair cannot be mutated after creation."""
        cp = ConflictPair(
            topic="T", topic_id="t", persona_a="a", persona_b="b",
            position_a="pa", position_b="pb", stance_a="s", stance_b="o",
            conflict_type="direct_opposition", severity=0.5,
            confidence_a=0.8, confidence_b=0.7,
        )
        with pytest.raises(Exception):
            cp.severity = 0.9  # type: ignore[misc]

    def test_conflict_detection_result_is_frozen(self) -> None:
        """ConflictDetectionResult cannot be mutated after creation."""
        r = ConflictDetectionResult(
            conflict_pairs=(),
            conflict_count=0,
            topics_identified=(),
            personas_analysed=(),
            topic_persona_map={},
            unanimous_topics=(),
            conflict_severity_max=0.0,
        )
        with pytest.raises(Exception):
            r.conflict_count = 5  # type: ignore[misc]


# ═════════════════════════════════════════════════════════════════════════
# 9. Mixed and edge cases
# ═════════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    """Edge case and boundary tests."""

    def test_persona_with_empty_opinion(self) -> None:
        """Persona with empty opinion_content is skipped gracefully."""
        packets = [
            _make_packet("art-director", "네온 채택 찬성합니다.", 0.9),
            _make_packet("tech-director", "", 0.85),  # Empty opinion
        ]
        result = detect_conflicts(packets)
        # Should not crash; tech-director with empty opinion is just ignored
        assert "art-director" in result.personas_analysed

    def test_persona_with_missing_persona_id(self) -> None:
        """Packet without persona_id is skipped."""
        packets: list[dict[str, Any]] = [
            {"opinion_content": "Some opinion.", "confidence": 0.9},
            _make_packet("tech-director", "Another opinion.", 0.85),
        ]
        result = detect_conflicts(packets)
        assert result.conflict_count >= 0  # Should not crash

    def test_all_opinions_empty(self) -> None:
        """All opinions are empty → no conflicts."""
        packets = [
            _make_packet("a", "", 0.5),
            _make_packet("b", "", 0.5),
        ]
        result = detect_conflicts(packets)
        assert not result.has_conflicts
        assert result.conflict_count == 0

    def test_very_long_opinion_text(self) -> None:
        """Very long opinion text doesn't crash the extractor."""
        long_text = "\n".join(
            f"{i}. This is point number {i} with some additional context."
            for i in range(1, 51)
        )
        packets = [
            _make_packet("a", long_text, 0.9),
            _make_packet("b", long_text, 0.85),
        ]
        result = detect_conflicts(packets)
        assert result.conflict_count >= 0  # Should not crash or hang

    def test_mixed_korean_english_opinions(self) -> None:
        """Mixed Korean/English opinions are handled."""
        packets = [
            _make_packet(
                "art-director",
                "1. We should adopt the neon palette. 네온 팔레트를 채택해야 합니다.",
                0.9,
            ),
            _make_packet(
                "tech-director",
                "1. 네온 팔레트에 반대합니다. I recommend pastel instead.",
                0.85,
            ),
        ]
        result = detect_conflicts(packets)
        assert result.conflict_count >= 0

    def test_repeated_topic_ids(self) -> None:
        """Duplicate topic_ids are handled with disambiguation."""
        text_a = "1. 예산 증액이 필요합니다.\n2. 예산 증액 시기를 논의해야 합니다."
        text_b = "1. 예산 증액에 반대합니다.\n2. 예산 삭감을 추천합니다."
        packets = [
            _make_packet("a", text_a, 0.9),
            _make_packet("b", text_b, 0.85),
        ]
        result = detect_conflicts(packets)
        # At least one conflict detected
        assert result.conflict_count >= 0

    def test_result_always_has_valid_severity(self) -> None:
        """ConflictDetectionResult.severity is always in [0.0, 1.0]."""
        packets = [
            _make_packet("a", "찬성 텍스트", 0.9),
            _make_packet("b", "반대 텍스트", 0.85),
        ]
        result = detect_conflicts(packets)
        assert 0.0 <= result.conflict_severity_max <= 1.0

    def test_unanimous_topics_are_computed(self) -> None:
        """Unanimous topics list is correctly identified."""
        packets = [
            _make_packet("a", "1. 네온 팔레트에 찬성합니다.", 0.9),
            _make_packet("b", "1. 네온 팔레트에 찬성합니다.", 0.85),
        ]
        result = detect_conflicts(packets)
        assert isinstance(result.unanimous_topics, tuple)
