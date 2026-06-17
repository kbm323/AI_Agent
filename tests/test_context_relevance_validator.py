"""Comprehensive tests for the context relevance validator.

Sub-AC 6.2b: Context relevance validation — module that evaluates LLM
response coherence against the meeting context (agenda relevance, topic
alignment, off-topic detection, reference consistency); independently
testable with (response, meeting_context) input pairs without requiring
persona definitions.

Test coverage:
- All four dimensions individually (agenda relevance, topic alignment,
  off-topic detection, reference consistency)
- Fully relevant response (baseline pass)
- Completely off-topic response detects irrelevance
- Response with partial tag coverage only
- Response referencing non-existent decisions/rounds
- Edge cases: empty response, empty context, None context, non-dict
  context, missing fields
- Mixed-language content
- ContextRelevanceReport properties (violation_count,
  critical_violations, violations_by_dimension, to_dict)
- Response length tracking
- Score boundaries (0.0 floor, 1.0 ceiling)
"""

from __future__ import annotations

import pytest

from src.context_relevance_validator import (
    ContextRelevanceReport,
    RelevanceViolation,
    validate_context_relevance,
)


# ═══════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════


def _valid_context(**overrides: object) -> dict[str, object]:
    """Return a valid meeting context dict for testing."""
    defaults: dict[str, object] = {
        "meeting_id": "meeting_20260610_abc123def456",
        "state": "in_meeting",
        "priority": "p2",
        "agenda": (
            "뮤직비디오 오프닝 시퀀스에 대한 비주얼 컨셉 아이디에이션. "
            "신규 싱글 발매를 위한 티저 콘텐츠 기획."
        ),
        "agenda_type": "creative_production",
        "tags": [
            "music-video",
            "visual-concept",
            "opening-sequence",
            "teaser-content",
            "brand-identity",
        ],
        "risk_tags": ["brand"],
        "round_count": 1,
        "round_context": (
            "아트 디렉터로서 뮤직비디오 오프닝의 비주얼 컨셉을 제안해주세요. "
            "색감, 구도, 타이포그래피, 브랜드 아이덴티티를 고려하세요."
        ),
        "token_limit_worker": 12000,
        "required_roles": [
            "art-director",
            "content-director",
            "marketing-lead",
        ],
        "optional_roles": ["tech-director", "validator"],
        "decisions": [
            {
                "decision_id": "dec_001",
                "role_id": "art-director",
                "content": "네온 느와르 팔레트 채택",
                "round": 1,
            },
            {
                "decision_id": "dec_002",
                "role_id": "content-director",
                "content": "스토리텔링 기반 오프닝",
                "round": 1,
            },
        ],
        "max_rounds": 3,
    }
    defaults.update(overrides)
    return defaults


def _relevant_response() -> str:
    """Return a response highly relevant to the default meeting context."""
    return (
        "네온 느와르 스타일의 비주얼 컨셉을 제안합니다. "
        "뮤직비디오 오프닝 시퀀스는 고대비 색감과 실루엣 중심의 "
        "구도로 구성하여 브랜드 아이덴티티를 강조합니다. "
        "타이포그래피는 미니멀 산세리프를 사용하고, 티저 콘텐츠는 "
        "15초 컷의 인트로 영상으로 기획합니다. "
        "이는 음악 장르와의 조화를 최우선으로 한 결정입니다."
    )


def _off_topic_response() -> str:
    """Return a completely off-topic response (about cooking)."""
    return (
        "오늘 저녁 메뉴로는 파스타가 좋을 것 같습니다. "
        "레시피는 토마토 소스 베이스에 바질을 곁들인 "
        "클래식한 스타일을 추천드립니다. "
        "와인은 레드 와인이 잘 어울리겠네요. "
        "디저트로는 티라미수를 준비하면 완벽한 식사가 될 것입니다."
    )


# ═══════════════════════════════════════════════════════════════════
# 1. Fully relevant response — baseline pass
# ═══════════════════════════════════════════════════════════════════


class TestFullyRelevantResponse:
    """Verify that a context-aligned response passes validation."""

    def test_relevant_response_passes(self) -> None:
        result = validate_context_relevance(
            _relevant_response(), _valid_context()
        )
        assert result.passed
        assert result.overall_score > 0.70
        assert result.agenda_relevance_score > 0.30
        assert result.topic_alignment_score > 0.30
        assert result.off_topic_score >= 0.90
        assert result.violation_count == 0

    def test_relevant_response_no_critical_violations(self) -> None:
        result = validate_context_relevance(
            _relevant_response(), _valid_context()
        )
        assert result.critical_violations == 0

    def test_relevant_response_length_tracked(self) -> None:
        response = _relevant_response()
        result = validate_context_relevance(response, _valid_context())
        assert result.response_length == len(response.strip())

    def test_relevant_response_scores_within_bounds(self) -> None:
        result = validate_context_relevance(
            _relevant_response(), _valid_context()
        )
        for attr in [
            "overall_score",
            "agenda_relevance_score",
            "topic_alignment_score",
            "off_topic_score",
            "reference_consistency_score",
        ]:
            val = getattr(result, attr)
            assert 0.0 <= val <= 1.0, f"{attr} = {val} out of [0, 1]"


# ═══════════════════════════════════════════════════════════════════
# 2. Completely off-topic response
# ═══════════════════════════════════════════════════════════════════


class TestOffTopicResponse:
    """Verify that a response unrelated to the agenda fails."""

    def test_off_topic_response_fails(self) -> None:
        result = validate_context_relevance(
            _off_topic_response(), _valid_context()
        )
        assert not result.passed
        assert result.overall_score < 0.50

    def test_off_topic_response_has_agenda_violations(self) -> None:
        result = validate_context_relevance(
            _off_topic_response(), _valid_context()
        )
        agenda_violations = [
            v for v in result.violations
            if v.dimension == "agenda_relevance"
        ]
        assert len(agenda_violations) > 0

    def test_off_topic_response_has_off_topic_violations(self) -> None:
        result = validate_context_relevance(
            _off_topic_response(), _valid_context()
        )
        off_violations = [
            v for v in result.violations
            if v.dimension == "off_topic"
        ]
        assert len(off_violations) > 0

    def test_off_topic_response_low_agenda_score(self) -> None:
        result = validate_context_relevance(
            _off_topic_response(), _valid_context()
        )
        assert result.agenda_relevance_score < 0.40

    def test_off_topic_response_low_topic_score(self) -> None:
        result = validate_context_relevance(
            _off_topic_response(), _valid_context()
        )
        assert result.topic_alignment_score < 0.40

    def test_off_topic_response_low_off_topic_score(self) -> None:
        result = validate_context_relevance(
            _off_topic_response(), _valid_context()
        )
        # off_topic_score should be < 1.0 when off-topic content detected
        assert result.off_topic_score < 1.0


# ═══════════════════════════════════════════════════════════════════
# 3. Response with partial tag coverage only
# ═══════════════════════════════════════════════════════════════════


class TestPartialTagCoverage:
    """Verify behavior when topic tags are only partially covered."""

    def test_partial_tag_coverage_score(self) -> None:
        response = "비주얼 컨셉에 대한 간단한 생각입니다."
        ctx = _valid_context()
        result = validate_context_relevance(response, ctx)
        # Should have some topic alignment but not full
        assert 0.10 <= result.topic_alignment_score <= 0.70

    def test_no_tag_hits_returns_zero(self) -> None:
        response = "일반적인 업무 보고입니다. 특별한 내용 없습니다."
        ctx = _valid_context()
        result = validate_context_relevance(response, ctx)
        assert result.topic_alignment_score < 0.20


# ═══════════════════════════════════════════════════════════════════
# 4. Reference consistency — hallucinated references
# ═══════════════════════════════════════════════════════════════════


class TestReferenceConsistency:
    """Verify detection of inconsistent/hallucinated references."""

    def test_round_exceeds_round_count(self) -> None:
        response = (
            "Round 5에서 논의한 내용을 바탕으로 "
            "Round 6의 결정을 제안합니다."
        )
        ctx = _valid_context(round_count=1)
        result = validate_context_relevance(response, ctx)
        assert result.reference_consistency_score < 1.0
        ref_violations = [
            v for v in result.violations
            if v.dimension == "reference_consistency"
        ]
        assert len(ref_violations) > 0

    def test_previous_round_when_none_exist(self) -> None:
        response = "이전 라운드에서 합의된 사항을 반영하여..."
        ctx = _valid_context(round_count=0)
        result = validate_context_relevance(response, ctx)
        ref_violations = [
            v for v in result.violations
            if v.dimension == "reference_consistency"
        ]
        assert len(ref_violations) > 0

    def test_unknown_role_reference(self) -> None:
        response = "finance-director의 의견에 동의합니다."
        ctx = _valid_context()
        result = validate_context_relevance(response, ctx)
        assert result.reference_consistency_score < 1.0

    def test_known_role_reference_passes(self) -> None:
        response = "art-director의 의견에 동의하며 content-director와 협업이 필요합니다."
        ctx = _valid_context()
        result = validate_context_relevance(response, ctx)
        # Known roles should not trigger violations
        assert result.reference_consistency_score >= 0.80

    def test_nonexistent_decision_reference(self) -> None:
        response = "decision_dec_999에서 결정된 사항을 참고하여..."
        ctx = _valid_context()
        result = validate_context_relevance(response, ctx)
        assert result.reference_consistency_score < 1.0

    def test_existing_decision_reference_passes(self) -> None:
        response = "decision_dec_001에서 채택된 네온 느와르 팔레트를 기반으로..."
        ctx = _valid_context()
        result = validate_context_relevance(response, ctx)
        # Known decision should not trigger violations
        assert result.reference_consistency_score >= 0.80


# ═══════════════════════════════════════════════════════════════════
# 5. Edge cases — empty / missing inputs
# ═══════════════════════════════════════════════════════════════════


class TestEdgeCases:
    """Verify graceful handling of edge-case inputs."""

    def test_empty_response(self) -> None:
        result = validate_context_relevance("", _valid_context())
        assert not result.passed
        assert result.overall_score == 0.0
        assert result.response_length == 0
        assert result.critical_violations > 0

    def test_whitespace_only_response(self) -> None:
        result = validate_context_relevance("   \n\t  \n", _valid_context())
        assert not result.passed
        assert result.overall_score == 0.0

    def test_none_context(self) -> None:
        result = validate_context_relevance("some response", None)
        assert not result.passed
        assert result.overall_score == 0.0
        assert result.critical_violations > 0

    def test_non_dict_context(self) -> None:
        result = validate_context_relevance("some response", "not_a_dict")  # type: ignore[arg-type]
        assert not result.passed
        assert result.overall_score == 0.0

    def test_empty_context_dict(self) -> None:
        result = validate_context_relevance("some response", {})
        assert result.agenda_relevance_score == 0.5  # warning for missing agenda
        # Topic alignment should return 0.5 (warning, no tags)
        assert result.topic_alignment_score == 0.5

    def test_minimal_response_with_minimal_context(self) -> None:
        ctx = {"agenda": "test", "tags": ["test"]}
        result = validate_context_relevance("test response", ctx)
        assert 0.0 <= result.overall_score <= 1.0

    def test_long_response_no_penalty_when_relevant(self) -> None:
        long_relevant = (
            "뮤직비디오 오프닝 시퀀스에 대한 상세한 비주얼 컨셉 제안입니다. "
            * 20
        )
        result = validate_context_relevance(
            long_relevant, _valid_context()
        )
        # Long relevant responses should still score well
        assert result.agenda_relevance_score > 0.30

    def test_critical_violation_causes_fail_even_with_high_score(self) -> None:
        # Force a critical violation but with high scores from other dims
        ctx = _valid_context(
            agenda="뮤직비디오 오프닝 비주얼 컨셉",
            tags=["visual"],
        )
        # Empty response triggers critical violation
        result = validate_context_relevance("", ctx)
        assert not result.passed
        assert result.critical_violations > 0


# ═══════════════════════════════════════════════════════════════════
# 6. Mixed-language content
# ═══════════════════════════════════════════════════════════════════


class TestMixedLanguage:
    """Verify the validator handles mixed Korean/English content."""

    def test_korean_response_with_english_tags(self) -> None:
        response = (
            "music-video의 visual-concept으로 neon-noir 스타일을 "
            "제안합니다. opening-sequence는 teaser-content로 활용."
        )
        ctx = _valid_context()
        result = validate_context_relevance(response, ctx)
        assert result.passed
        assert result.topic_alignment_score > 0.50

    def test_english_response_with_korean_agenda(self) -> None:
        response = (
            "I suggest a neon-noir visual concept for the music video "
            "opening sequence. The brand identity should be emphasized "
            "through high-contrast colors and silhouette composition."
        )
        ctx = _valid_context()
        result = validate_context_relevance(response, ctx)
        assert result.agenda_relevance_score > 0.0


# ═══════════════════════════════════════════════════════════════════
# 7. ContextRelevanceReport properties
# ═══════════════════════════════════════════════════════════════════


class TestReportProperties:
    """Verify the report's convenience properties and methods."""

    def test_violation_count_zero_for_clean_pass(self) -> None:
        result = validate_context_relevance(
            _relevant_response(), _valid_context()
        )
        assert result.violation_count == 0
        assert result.critical_violations == 0

    def test_violations_by_dimension_grouping(self) -> None:
        response = _off_topic_response()
        result = validate_context_relevance(response, _valid_context())
        grouped = result.violations_by_dimension()
        assert isinstance(grouped, dict)
        assert "agenda_relevance" in grouped

    def test_to_dict_serializable(self) -> None:
        result = validate_context_relevance(
            _relevant_response(), _valid_context()
        )
        d = result.to_dict()
        assert isinstance(d, dict)
        assert "passed" in d
        assert "overall_score" in d
        assert isinstance(d["violations"], list)

    def test_threshold_propagated(self) -> None:
        result = validate_context_relevance(
            "response text", _valid_context(), threshold=0.85
        )
        assert result.threshold == 0.85


# ═══════════════════════════════════════════════════════════════════
# 8. Immutability and data class integrity
# ═══════════════════════════════════════════════════════════════════


class TestDataClassImmutability:
    """Verify that report and violation objects are frozen."""

    def test_report_is_frozen(self) -> None:
        result = validate_context_relevance(
            _relevant_response(), _valid_context()
        )
        with pytest.raises(Exception):
            result.passed = False  # type: ignore[misc]

    def test_violation_is_frozen(self) -> None:
        v = RelevanceViolation(
            dimension="agenda_relevance",
            severity="critical",
            message="test",
        )
        with pytest.raises(Exception):
            v.message = "changed"  # type: ignore[misc]


# ═══════════════════════════════════════════════════════════════════
# 9. Each dimension individually (detailed)
# ═══════════════════════════════════════════════════════════════════


class TestAgendaRelevanceDimension:
    """Detailed tests for the agenda relevance dimension."""

    def test_exact_agenda_match_scores_high(self) -> None:
        response = (
            "뮤직비디오 오프닝 시퀀스 비주얼 컨셉 아이디에이션: "
            "신규 싱글 티저 콘텐츠 기획안"
        )
        ctx = _valid_context()
        result = validate_context_relevance(response, ctx)
        assert result.agenda_relevance_score > 0.50

    def test_no_agenda_in_context_returns_warning(self) -> None:
        ctx = _valid_context(agenda="")
        result = validate_context_relevance("some response", ctx)
        assert result.agenda_relevance_score == 0.5
        violations = [
            v for v in result.violations
            if "no agenda" in v.message.lower()
        ]
        assert len(violations) > 0

    def test_score_at_least_zero(self) -> None:
        result = validate_context_relevance(
            "hello world", _valid_context()
        )
        assert result.agenda_relevance_score >= 0.0

    def test_score_at_most_one(self) -> None:
        # Repeat agenda words exactly
        response = "뮤직비디오 오프닝 시퀀스 뮤직비디오 오프닝 시퀀스"
        ctx = _valid_context()
        result = validate_context_relevance(response, ctx)
        assert result.agenda_relevance_score <= 1.0


class TestTopicAlignmentDimension:
    """Detailed tests for the topic alignment dimension."""

    def test_no_tags_context_returns_warning(self) -> None:
        ctx = _valid_context(tags=[])
        result = validate_context_relevance("response", ctx)
        assert result.topic_alignment_score == 0.5

    def test_all_tags_hit_scores_high(self) -> None:
        # Use all defined tags in response
        response = (
            "music-video visual-concept opening-sequence "
            "teaser-content brand-identity"
        )
        ctx = _valid_context()
        result = validate_context_relevance(response, ctx)
        assert result.topic_alignment_score > 0.80

    def test_tag_partial_substring_match(self) -> None:
        response = "뮤직비디오 오프닝에 visual concept을 적용"
        ctx = _valid_context(tags=["visual-concept"])
        result = validate_context_relevance(response, ctx)
        # "visual concept" should partial-match "visual-concept"
        assert result.topic_alignment_score > 0.0


class TestOffTopicDimension:
    """Detailed tests for the off-topic detection dimension."""

    def test_cooking_domain_detected(self) -> None:
        response = "레시피와 요리법에 관한 내용입니다."
        ctx = _valid_context()
        result = validate_context_relevance(response, ctx)
        assert result.off_topic_score < 1.0

    def test_sports_domain_detected(self) -> None:
        response = "축구 경기 분석과 스포츠 전략입니다."
        ctx = _valid_context()
        result = validate_context_relevance(response, ctx)
        assert result.off_topic_score < 1.0

    def test_medical_domain_detected(self) -> None:
        response = "환자 진단과 의학적 처방에 관한 내용입니다."
        ctx = _valid_context()
        result = validate_context_relevance(response, ctx)
        assert result.off_topic_score < 1.0

    def test_political_domain_detected(self) -> None:
        response = "선거 전략과 정치적 캠페인에 관한 내용입니다."
        ctx = _valid_context()
        result = validate_context_relevance(response, ctx)
        assert result.off_topic_score < 1.0

    def test_multiple_off_topic_domains_stacked_penalty(self) -> None:
        response = (
            "환자 진단 결과를 바탕으로 요리법을 추천하며 "
            "축구 경기 분석도 병행합니다."
        )
        ctx = _valid_context()
        result = validate_context_relevance(response, ctx)
        # Multiple domains should stack penalties
        assert result.off_topic_score <= 0.60

    def test_relevant_domain_not_flagged(self) -> None:
        response = _relevant_response()
        ctx = _valid_context()
        result = validate_context_relevance(response, ctx)
        assert result.off_topic_score >= 0.90


class TestReferenceConsistencyDimension:
    """Detailed tests for the reference consistency dimension."""

    def test_no_round_references_score_max(self) -> None:
        response = "새로운 제안입니다."
        ctx = _valid_context(round_count=3)
        result = validate_context_relevance(response, ctx)
        assert result.reference_consistency_score == 1.0

    def test_round_within_bounds_passes(self) -> None:
        response = "Round 1의 결정을 기반으로 제안합니다."
        ctx = _valid_context(round_count=3)
        result = validate_context_relevance(response, ctx)
        assert result.reference_consistency_score == 1.0

    def test_default_round_count_zero(self) -> None:
        response = "Round 2에서 논의된 내용..."
        ctx = _valid_context()
        del ctx["round_count"]
        result = validate_context_relevance(response, ctx)
        # round_count not present -> treated as 0 -> Round 2 exceeds
        assert result.reference_consistency_score < 1.0


# ═══════════════════════════════════════════════════════════════════
# 10. Weight composition verification
# ═══════════════════════════════════════════════════════════════════


class TestWeightComposition:
    """Verify overall_score equals weighted sum of dimension scores."""

    def test_overall_is_weighted_sum(self) -> None:
        result = validate_context_relevance(
            _relevant_response(), _valid_context()
        )
        expected = (
            result.agenda_relevance_score * 0.30
            + result.topic_alignment_score * 0.25
            + result.off_topic_score * 0.25
            + result.reference_consistency_score * 0.20
        )
        assert abs(result.overall_score - round(expected, 4)) < 0.01

    def test_overall_weighted_for_off_topic_response(self) -> None:
        result = validate_context_relevance(
            _off_topic_response(), _valid_context()
        )
        expected = (
            result.agenda_relevance_score * 0.30
            + result.topic_alignment_score * 0.25
            + result.off_topic_score * 0.25
            + result.reference_consistency_score * 0.20
        )
        assert abs(result.overall_score - round(expected, 4)) < 0.01


# ═══════════════════════════════════════════════════════════════════
# 11. RelevanceViolation data class
# ═══════════════════════════════════════════════════════════════════


class TestRelevanceViolationDataClass:
    """Verify the RelevanceViolation data class."""

    def test_create_with_minimal_fields(self) -> None:
        v = RelevanceViolation(
            dimension="agenda_relevance",
            severity="warning",
            message="test message",
        )
        assert v.dimension == "agenda_relevance"
        assert v.severity == "warning"
        assert v.message == "test message"
        assert v.detail == ""

    def test_create_with_detail(self) -> None:
        v = RelevanceViolation(
            dimension="off_topic",
            severity="major",
            message="off-topic detected",
            detail="keyword: recipe",
        )
        assert v.detail == "keyword: recipe"

    def test_equality(self) -> None:
        v1 = RelevanceViolation("x", "major", "msg")
        v2 = RelevanceViolation("x", "major", "msg")
        assert v1 == v2
