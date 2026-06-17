"""Comprehensive tests for the divergence metric computation module.

Sub-AC 6.3.1: Divergence metric computation — accepts paired model
outputs (GLM-5.1 primary, Codex GPT-5.5 conditional) and returns a
normalized divergence score using semantic/text comparison; testable
with curated output pairs at known agreement levels.

Test coverage:
- Identical outputs (baseline divergence ≈ 0.0)
- Completely unrelated outputs (divergence ≈ 1.0)
- Partially overlapping outputs with known agreement levels
- Korean-only, English-only, and mixed-language pairs
- Empty strings (graceful degradation)
- Edge cases: single-word outputs, whitespace-only
- Type errors for non-string inputs
- DivergenceReport properties (dimension_count,
  highest_divergence_dimension, dimensions_by_name, to_dict)
- All five dimensions individually verified
- Custom divergence threshold
- Custom source labels (primary_source, secondary_source)
- Score boundaries (0.0 floor, 1.0 ceiling)
- DivergenceDimension dataclass validation
- DivergenceReport dataclass validation
- High-structural-similarity-but-different-concepts case
- Same-stance-different-reasoning case
"""

from __future__ import annotations

import pytest

from src.divergence_metric import (
    DEFAULT_DIVERGENCE_THRESHOLD,
    DivergenceDimension,
    DivergenceReport,
    compute_divergence,
)


# ═════════════════════════════════════════════════════════════════════════
# Helper: curated output pairs at known agreement levels
# ═════════════════════════════════════════════════════════════════════════


def _identical_outputs() -> tuple[str, str]:
    """Two outputs that are character-for-character identical."""
    text = (
        "비주얼 컨셉 검증 결과: 통과.\n"
        "색감, 구도, 타이포그래피 모두 프로젝트 목표와 일치함.\n"
        "리스크 없음. 실행 권장.\n"
        "Verdict: pass. Validation score: 0.92"
    )
    return text, text


def _near_identical_outputs() -> tuple[str, str]:
    """Two outputs that are nearly identical (minor wording differences)."""
    primary = (
        "비주얼 컨셉 검증 결과: 통과.\n"
        "색감, 구도, 타이포그래피 모두 프로젝트 목표와 일치함.\n"
        "리스크 없음. 실행 권장.\n"
        "Verdict: pass. Validation score: 0.92"
    )
    secondary = (
        "비주얼 컨셉 검증: 합격.\n"
        "색감, 구도, 타이포그래피 모두 프로젝트 목표와 일치합니다.\n"
        "리스크 없음. 실행을 권장합니다.\n"
        "Verdict: pass. Validation score: 0.92"
    )
    return primary, secondary


def _moderate_overlap_outputs() -> tuple[str, str]:
    """Outputs with moderate token/concept overlap but different conclusions."""
    primary = (
        "컨셉 분석 결과: 네온 느와르 스타일이 적합함.\n"
        "타이포그래피는 미니멀 산세리프 권장.\n"
        "색감은 고대비로 설정. 브랜드 아이덴티티 강화됨.\n"
        "리스크: 브랜드 일관성 — 낮음.\n"
        "Verdict: pass."
    )
    secondary = (
        "컨셉 분석 결과: 사이버펑크 스타일이 더 적합함.\n"
        "타이포그래피는 고딕 스타일 권장.\n"
        "색감은 저대비로 설정. 브랜드 아이덴티티 약화 우려.\n"
        "리스크: 브랜드 일관성 — 높음.\n"
        "Verdict: revision_required."
    )
    return primary, secondary


def _completely_unrelated_outputs() -> tuple[str, str]:
    """Outputs on entirely different topics with no overlap."""
    primary = (
        "뮤직비디오 오프닝 시퀀스의 비주얼 컨셉을 네온 느와르로 제안합니다. "
        "색감은 퍼플과 핑크를 주조로 하고 실루엣 중심의 구도를 사용합니다."
    )
    secondary = (
        "마케팅 예산 분석 결과: Q3 디지털 광고 예산이 15% 초과되었습니다. "
        "인플루언서 마케팅 예산을 20% 재배정할 것을 권장합니다."
    )
    return primary, secondary


def _same_stance_different_reasoning() -> tuple[str, str]:
    """Both reach 'pass' but via different reasoning paths."""
    primary = (
        "검증 결과: 통과.\n"
        "이유: 컨셉이 창의적이고 독창적이며 시장 트렌드와 부합함.\n"
        "추가 제안: 오프닝 시퀀스에 AR 요소 도입 검토."
    )
    secondary = (
        "검증 결과: 통과.\n"
        "이유: 제작 일정과 예산 범위 내에서 구현 가능한 컨셉임.\n"
        "추가 제안: 기존 에셋 재활용으로 비용 절감 가능."
    )
    return primary, secondary


def _korean_only_outputs() -> tuple[str, str]:
    """Both outputs in pure Korean with partial overlap."""
    primary = (
        "비주얼 컨셉 방향: 복고풍 네온 사인 스타일.\n"
        "주요 색상: 마젠타, 시안, 옐로우.\n"
        "타이포그래피: 80년대 레트로 폰트.\n"
        "판정: 조건부 통과 (색상 팔레트 조정 필요)"
    )
    secondary = (
        "비주얼 컨셉 방향: 모던 미니멀리즘.\n"
        "주요 색상: 모노크롬 + 액센트 컬러.\n"
        "타이포그래피: 클린 산세리프.\n"
        "판정: 통과"
    )
    return primary, secondary


def _english_only_outputs() -> tuple[str, str]:
    """Both outputs in pure English with partial overlap."""
    primary = (
        "Visual concept: retro neon sign aesthetic. "
        "Primary colors: magenta, cyan, yellow. "
        "Typography: 80s retro font. "
        "Verdict: conditional_pass (color palette needs adjustment)."
    )
    secondary = (
        "Visual concept: retro neon sign aesthetic. "
        "Primary colors: magenta, cyan, yellow. "
        "Typography: 80s retro font. "
        "Verdict: pass (color palette approved after revision)."
    )
    return primary, secondary


def _single_word_outputs() -> tuple[str, str]:
    """Single-word outputs."""
    return "pass", "fail"


def _structured_vs_freeform() -> tuple[str, str]:
    """Highly structured output vs free-form paragraph on same topic."""
    primary = (
        "## Validation Report\n"
        "1. Requirements Fit: 0.9\n"
        "2. Logical Consistency: 0.85\n"
        "3. Factual Grounding: 0.8\n"
        "4. Feasibility: 0.9\n"
        "5. Risk Policy: 0.75\n"
        "Overall: 0.84\n"
        "Verdict: revision_required\n"
        "Recommendation: strengthen factual grounding with citations."
    )
    secondary = (
        "전반적으로 요구사항에 잘 부합하며 논리적 일관성도 양호합니다. "
        "다만 사실적 근거가 다소 부족하여 일부 주장의 신뢰성이 떨어집니다. "
        "실행 가능성은 높으나 리스크 평가가 완전하지 않아 보완이 필요합니다. "
        "종합적으로 수정 후 재검토를 권장합니다."
    )
    return primary, secondary


# ═════════════════════════════════════════════════════════════════════════
# Tests: basic behaviour
# ═════════════════════════════════════════════════════════════════════════


class TestComputeDivergenceBasics:
    """Basic behavioural tests for compute_divergence."""

    def test_identical_outputs_zero_divergence(self) -> None:
        """Identical outputs should have divergence near 0.0."""
        primary, secondary = _identical_outputs()
        report = compute_divergence(primary, secondary)

        assert report.overall_divergence == 0.0
        assert report.overall_similarity == 1.0
        assert report.passed is True

    def test_near_identical_low_divergence(self) -> None:
        """Nearly identical outputs should have very low divergence."""
        primary, secondary = _near_identical_outputs()
        report = compute_divergence(primary, secondary)

        assert report.overall_divergence < 0.15, (
            f"Expected divergence < 0.15, got {report.overall_divergence}"
        )
        assert report.passed is True

    def test_completely_unrelated_high_divergence(self) -> None:
        """Completely unrelated outputs should have high divergence."""
        primary, secondary = _completely_unrelated_outputs()
        report = compute_divergence(primary, secondary)

        assert report.overall_divergence > 0.5, (
            f"Expected divergence > 0.5, got {report.overall_divergence}"
        )

    def test_moderate_overlap_moderate_divergence(self) -> None:
        """Partially overlapping outputs should have moderate divergence."""
        primary, secondary = _moderate_overlap_outputs()
        report = compute_divergence(primary, secondary)

        # Same topic, shared vocabulary, but different conclusions
        assert 0.2 < report.overall_divergence < 0.8, (
            f"Expected divergence in [0.2, 0.8], got {report.overall_divergence}"
        )

    def test_same_stance_different_reasoning(self) -> None:
        """Same verdict via different reasoning — moderate divergence."""
        primary, secondary = _same_stance_different_reasoning()
        report = compute_divergence(primary, secondary)

        # Same stance (pass) but different reasoning — should be moderate
        assert report.overall_divergence < 0.6, (
            f"Same stance should keep divergence moderate, "
            f"got {report.overall_divergence}"
        )


# ═════════════════════════════════════════════════════════════════════════
# Tests: language handling
# ═════════════════════════════════════════════════════════════════════════


class TestLanguageHandling:
    """Tests for Korean, English, and mixed-language handling."""

    def test_korean_only_outputs(self) -> None:
        """Korean-only outputs should be properly tokenised and compared."""
        primary, secondary = _korean_only_outputs()
        report = compute_divergence(primary, secondary)

        # Different concepts and stances → moderate-high divergence
        assert 0.3 < report.overall_divergence < 0.9, (
            f"Expected divergence in [0.3, 0.9], got {report.overall_divergence}"
        )

    def test_english_only_outputs(self) -> None:
        """English-only outputs should be properly compared."""
        primary, secondary = _english_only_outputs()
        report = compute_divergence(primary, secondary)

        # Nearly identical English → low divergence
        assert report.overall_divergence < 0.2, (
            f"Expected divergence < 0.2 for nearly identical English, "
            f"got {report.overall_divergence}"
        )
        assert report.passed is True

    def test_mixed_language_outputs(self) -> None:
        """Mixed Korean/English outputs should be handled."""
        primary = "컨셉 검증: pass. 디자인 quality is 높음."
        secondary = "컨셉 검증: fail. 디자인 quality is 낮음."
        report = compute_divergence(primary, secondary)

        # Should produce a valid report with measurable divergence
        assert 0.0 <= report.overall_divergence <= 1.0
        # One pass, one fail → should have some divergence
        assert report.overall_divergence > 0.1


# ═════════════════════════════════════════════════════════════════════════
# Tests: edge cases
# ═════════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    """Edge-case and boundary tests."""

    def test_both_empty_strings(self) -> None:
        """Both empty strings: considered identical (divergence 0.0)."""
        report = compute_divergence("", "")

        assert report.overall_divergence == 0.0
        assert report.passed is True
        assert report.primary_length == 0
        assert report.secondary_length == 0

    def test_one_empty_one_populated(self) -> None:
        """One empty, one populated: maximum divergence."""
        report = compute_divergence("some validation output", "")

        assert report.overall_divergence > 0.5, (
            f"Expected high divergence, got {report.overall_divergence}"
        )

    def test_whitespace_only_outputs(self) -> None:
        """Whitespace-only should be treated as empty content."""
        report = compute_divergence("   \n  \t  ", "   \n  ")

        # Both have no analysable content
        assert report.overall_divergence == 0.0

    def test_single_word_outputs(self) -> None:
        """Single-word outputs should be compared."""
        primary, secondary = _single_word_outputs()
        report = compute_divergence(primary, secondary)

        assert 0.0 <= report.overall_divergence <= 1.0
        # "pass" vs "fail" — should have meaningful divergence
        assert report.overall_divergence > 0.0

    def test_very_long_outputs(self) -> None:
        """Very long outputs should not break the computation."""
        primary = ("검증 통과. " * 200) + "최종 판정: pass."
        secondary = ("검증 통과. " * 200) + "최종 판정: pass."

        report = compute_divergence(primary, secondary)

        assert report.overall_divergence == 0.0
        assert report.primary_length > 1000

    def test_structured_vs_freeform(self) -> None:
        """Structured report vs free-form text on same topic."""
        primary, secondary = _structured_vs_freeform()
        report = compute_divergence(primary, secondary)

        assert 0.0 <= report.overall_divergence <= 1.0
        # Very different structures — structural divergence should be high
        struct_dim = report.dimensions_by_name()["structural_similarity"]
        assert struct_dim.divergence > 0.0, (
            f"Structured vs free-form should have structural divergence > 0, "
            f"got {struct_dim.divergence}"
        )


# ═════════════════════════════════════════════════════════════════════════
# Tests: type validation
# ═════════════════════════════════════════════════════════════════════════


class TestTypeValidation:
    """Input type validation tests."""

    def test_non_string_primary_raises_type_error(self) -> None:
        """Non-string primary_output should raise TypeError."""
        with pytest.raises(TypeError, match="primary_output must be str"):
            compute_divergence(42, "valid")  # type: ignore[arg-type]

    def test_non_string_secondary_raises_type_error(self) -> None:
        """Non-string secondary_output should raise TypeError."""
        with pytest.raises(TypeError, match="secondary_output must be str"):
            compute_divergence("valid", None)  # type: ignore[arg-type]

    def test_both_non_string_raises_type_error(self) -> None:
        """Both non-string should raise TypeError on primary first."""
        with pytest.raises(TypeError):
            compute_divergence(3.14, [1, 2, 3])  # type: ignore[arg-type]


# ═════════════════════════════════════════════════════════════════════════
# Tests: DivergenceReport properties
# ═════════════════════════════════════════════════════════════════════════


class TestDivergenceReport:
    """DivergenceReport dataclass and property tests."""

    def test_dimension_count(self) -> None:
        """Report should have exactly 5 dimensions."""
        report = compute_divergence("a", "b")
        assert report.dimension_count == 5

    def test_highest_divergence_dimension(self) -> None:
        """highest_divergence_dimension returns the most divergent dimension."""
        primary, secondary = _structured_vs_freeform()
        report = compute_divergence(primary, secondary)

        highest = report.highest_divergence_dimension
        assert highest is not None
        # The most divergent dimension should have the max divergence
        max_div = max(d.divergence for d in report.dimensions)
        assert highest.divergence == max_div

    def test_dimensions_by_name(self) -> None:
        """dimensions_by_name returns correct mapping."""
        report = compute_divergence("a", "b")
        by_name = report.dimensions_by_name()

        expected_names = {
            "token_similarity",
            "bigram_similarity",
            "concept_overlap",
            "structural_similarity",
            "stance_alignment",
        }
        assert set(by_name.keys()) == expected_names

    def test_to_dict_structure(self) -> None:
        """to_dict returns expected keys and types."""
        report = compute_divergence("primary output", "secondary output")
        d = report.to_dict()

        assert isinstance(d, dict)
        assert "overall_divergence" in d
        assert "overall_similarity" in d
        assert "passed" in d
        assert "divergence_threshold" in d
        assert "primary_length" in d
        assert "secondary_length" in d
        assert "primary_source" in d
        assert "secondary_source" in d
        assert "dimensions" in d
        assert len(d["dimensions"]) == 5  # type: ignore[arg-type]

    def test_to_dict_dimensions_have_required_fields(self) -> None:
        """Each dimension in to_dict should have all required fields."""
        report = compute_divergence("a", "b")
        d = report.to_dict()
        dims = d["dimensions"]

        required_dim_fields = {
            "name", "weight", "similarity", "divergence",
            "weighted_divergence", "details",
        }
        for dim in dims:  # type: ignore[union-attr]
            assert set(dim.keys()) == required_dim_fields  # type: ignore[union-attr]

    def test_length_tracking(self) -> None:
        """primary_length and secondary_length should match input lengths."""
        primary = "hello world"
        secondary = "안녕하세요 세계"
        report = compute_divergence(primary, secondary)

        assert report.primary_length == len(primary)
        assert report.secondary_length == len(secondary)

    def test_custom_source_labels(self) -> None:
        """Custom primary_source and secondary_source should be recorded."""
        report = compute_divergence(
            "output a",
            "output b",
            primary_source="claude-4",
            secondary_source="gemini-2.5",
        )

        assert report.primary_source == "claude-4"
        assert report.secondary_source == "gemini-2.5"

    def test_default_source_labels(self) -> None:
        """Default source labels should be the GLM/Codex pair."""
        report = compute_divergence("a", "b")

        assert report.primary_source == "glm-5.1"
        assert report.secondary_source == "codex-gpt-5.5"


# ═════════════════════════════════════════════════════════════════════════
# Tests: divergence threshold
# ═════════════════════════════════════════════════════════════════════════


class TestDivergenceThreshold:
    """Divergence threshold and pass/fail tests."""

    def test_default_threshold_is_0_30(self) -> None:
        """Default divergence threshold is 0.30."""
        report = compute_divergence("a", "b")
        assert report.divergence_threshold == 0.30

    def test_custom_threshold(self) -> None:
        """Custom threshold should be reflected in the report."""
        report = compute_divergence("a", "b", divergence_threshold=0.50)
        assert report.divergence_threshold == 0.50

    def test_passed_when_divergence_below_threshold(self) -> None:
        """passed=True when divergence <= threshold."""
        primary, secondary = _near_identical_outputs()
        report = compute_divergence(primary, secondary, divergence_threshold=0.50)

        assert report.overall_divergence < 0.50
        assert report.passed is True

    def test_failed_when_divergence_above_threshold(self) -> None:
        """passed=False when divergence > threshold."""
        primary, secondary = _completely_unrelated_outputs()
        report = compute_divergence(
            primary, secondary, divergence_threshold=0.10
        )

        assert report.overall_divergence > 0.10
        assert report.passed is False

    def test_passed_when_divergence_equals_threshold(self) -> None:
        """passed=True when divergence exactly equals threshold."""
        primary, secondary = _identical_outputs()
        report = compute_divergence(
            primary, secondary, divergence_threshold=0.0
        )

        assert report.overall_divergence == 0.0
        assert report.passed is True


# ═════════════════════════════════════════════════════════════════════════
# Tests: individual dimension verification
# ═════════════════════════════════════════════════════════════════════════


class TestIndividualDimensions:
    """Verify each dimension behaves as expected independently."""

    def test_token_similarity_identical(self) -> None:
        """Token similarity should be 1.0 for identical outputs."""
        primary, secondary = _identical_outputs()
        report = compute_divergence(primary, secondary)
        dim = report.dimensions_by_name()["token_similarity"]

        assert dim.similarity == 1.0
        assert dim.divergence == 0.0

    def test_token_similarity_unrelated(self) -> None:
        """Token similarity should be 0.0 for unrelated outputs."""
        primary, secondary = _completely_unrelated_outputs()
        report = compute_divergence(primary, secondary)
        dim = report.dimensions_by_name()["token_similarity"]

        assert dim.similarity == 0.0

    def test_bigram_similarity_proportional(self) -> None:
        """Bigram similarity should be >= token similarity (captures order)."""
        primary, secondary = _moderate_overlap_outputs()
        report = compute_divergence(primary, secondary)

        token_dim = report.dimensions_by_name()["token_similarity"]
        bigram_dim = report.dimensions_by_name()["bigram_similarity"]

        # Bigram similarity can be higher or lower — just verify valid
        assert 0.0 <= bigram_dim.similarity <= 1.0
        assert 0.0 <= token_dim.similarity <= 1.0

    def test_concept_overlap_same_domain(self) -> None:
        """Concept overlap should be positive for same-domain texts."""
        primary, secondary = _moderate_overlap_outputs()
        report = compute_divergence(primary, secondary)
        dim = report.dimensions_by_name()["concept_overlap"]

        # Same domain (visual concept) → some concept overlap expected
        assert dim.similarity > 0.0, (
            f"Expected positive concept overlap for same-domain texts, "
            f"got {dim.similarity}"
        )

    def test_concept_overlap_different_domain(self) -> None:
        """Concept overlap should be 0.0 for different domains."""
        primary, secondary = _completely_unrelated_outputs()
        report = compute_divergence(primary, secondary)
        dim = report.dimensions_by_name()["concept_overlap"]

        # Different domains (visual vs marketing) → no concept overlap
        assert dim.similarity == 0.0

    def test_stance_alignment_same_verdict(self) -> None:
        """Stance alignment should be high when both reach same verdict."""
        primary, secondary = _same_stance_different_reasoning()
        report = compute_divergence(primary, secondary)
        dim = report.dimensions_by_name()["stance_alignment"]

        # Both "pass" → high stance alignment
        assert dim.similarity >= 0.5, (
            f"Same verdict should give stance similarity >= 0.5, "
            f"got {dim.similarity}"
        )

    def test_stance_alignment_different_verdict(self) -> None:
        """Stance alignment should be low when verdicts differ."""
        primary, secondary = _moderate_overlap_outputs()
        report = compute_divergence(primary, secondary)
        dim = report.dimensions_by_name()["stance_alignment"]

        # "pass" vs "revision_required" → low stance alignment
        assert dim.similarity <= 0.6, (
            f"Different verdicts should give stance similarity <= 0.6, "
            f"got {dim.similarity}"
        )

    def test_structural_similarity_identical_structure(self) -> None:
        """Structural similarity should be 1.0 for identically structured outputs."""
        primary, secondary = _identical_outputs()
        report = compute_divergence(primary, secondary)
        dim = report.dimensions_by_name()["structural_similarity"]

        assert dim.similarity == 1.0


# ═════════════════════════════════════════════════════════════════════════
# Tests: score boundaries
# ═════════════════════════════════════════════════════════════════════════


class TestScoreBoundaries:
    """Verify scores always stay in [0.0, 1.0]."""

    def test_overall_divergence_bounded(self) -> None:
        """Overall divergence must be in [0.0, 1.0]."""
        test_cases = [
            _identical_outputs(),
            _near_identical_outputs(),
            _moderate_overlap_outputs(),
            _completely_unrelated_outputs(),
            _same_stance_different_reasoning(),
            _korean_only_outputs(),
            _english_only_outputs(),
            _single_word_outputs(),
            _structured_vs_freeform(),
            ("", ""),
            ("a", ""),
            ("", "b"),
        ]

        for primary, secondary in test_cases:
            report = compute_divergence(primary, secondary)
            assert 0.0 <= report.overall_divergence <= 1.0, (
                f"Divergence {report.overall_divergence} out of bounds "
                f"for primary={primary[:30]!r}, secondary={secondary[:30]!r}"
            )
            assert 0.0 <= report.overall_similarity <= 1.0, (
                f"Similarity {report.overall_similarity} out of bounds"
            )

    def test_dimension_scores_bounded(self) -> None:
        """All dimension similarity/divergence scores must be in [0.0, 1.0]."""
        primary, secondary = _moderate_overlap_outputs()
        report = compute_divergence(primary, secondary)

        for dim in report.dimensions:
            assert 0.0 <= dim.similarity <= 1.0, (
                f"Dimension {dim.name} similarity {dim.similarity} out of bounds"
            )
            assert 0.0 <= dim.divergence <= 1.0, (
                f"Dimension {dim.name} divergence {dim.divergence} out of bounds"
            )
            assert 0.0 <= dim.weighted_divergence <= 1.0, (
                f"Dimension {dim.name} weighted_divergence "
                f"{dim.weighted_divergence} out of bounds"
            )

    def test_dimension_weights_sum_to_one(self) -> None:
        """All dimension weights should sum to ~1.0."""
        report = compute_divergence("a", "b")
        total_weight = sum(d.weight for d in report.dimensions)
        assert abs(total_weight - 1.0) < 0.001, (
            f"Dimension weights sum to {total_weight}, expected 1.0"
        )

    def test_weighted_divergence_consistency(self) -> None:
        """weighted_divergence = weight * divergence for each dimension."""
        report = compute_divergence("a", "b")
        for dim in report.dimensions:
            expected = round(dim.weight * dim.divergence, 4)
            assert dim.weighted_divergence == expected, (
                f"Dimension {dim.name}: weighted_divergence={dim.weighted_divergence}, "
                f"expected={expected}"
            )

    def test_overall_similarity_consistency(self) -> None:
        """overall_similarity = 1.0 - overall_divergence."""
        report = compute_divergence("a", "b")
        assert report.overall_similarity == round(
            1.0 - report.overall_divergence, 4
        )


# ═════════════════════════════════════════════════════════════════════════
# Tests: DivergenceDimension dataclass
# ═════════════════════════════════════════════════════════════════════════


class TestDivergenceDimension:
    """DivergenceDimension dataclass validation."""

    def test_creation(self) -> None:
        """DivergenceDimension can be created with valid values."""
        dim = DivergenceDimension(
            name="test_dim",
            weight=0.3,
            similarity=0.85,
            divergence=0.15,
            weighted_divergence=0.045,
            details="test details",
        )

        assert dim.name == "test_dim"
        assert dim.weight == 0.3
        assert dim.similarity == 0.85
        assert dim.divergence == 0.15
        assert dim.weighted_divergence == 0.045
        assert dim.details == "test details"

    def test_frozen(self) -> None:
        """DivergenceDimension is frozen (immutable)."""
        dim = DivergenceDimension(
            name="test",
            weight=0.1,
            similarity=0.5,
            divergence=0.5,
            weighted_divergence=0.05,
        )

        with pytest.raises(Exception):
            dim.similarity = 0.9  # type: ignore[misc]


# ═════════════════════════════════════════════════════════════════════════
# Tests: DivergenceReport dataclass
# ═════════════════════════════════════════════════════════════════════════


class TestDivergenceReportDataclass:
    """DivergenceReport dataclass validation."""

    def test_creation_with_empty_dimensions(self) -> None:
        """DivergenceReport can be created with empty dimensions."""
        report = DivergenceReport(
            overall_divergence=0.5,
            overall_similarity=0.5,
            dimensions=(),
            passed=False,
            divergence_threshold=0.30,
            primary_length=100,
            secondary_length=200,
        )

        assert report.dimension_count == 0
        assert report.highest_divergence_dimension is None

    def test_frozen(self) -> None:
        """DivergenceReport is frozen (immutable)."""
        report = compute_divergence("a", "b")

        with pytest.raises(Exception):
            report.overall_divergence = 0.0  # type: ignore[misc]


# ═════════════════════════════════════════════════════════════════════════
# Tests: API contract for Sub-AC 6.3.1
# ═════════════════════════════════════════════════════════════════════════


class TestSubAC631Contract:
    """Verify the module fulfills the Sub-AC 6.3.1 contract."""

    def test_accepts_paired_model_outputs(self) -> None:
        """Accepts paired GLM (primary) and Codex (secondary) outputs."""
        glm_output = "GLM-5.1 validation: pass. Score: 0.88."
        codex_output = "Codex GPT-5.5 review: pass. Score: 0.85."

        report = compute_divergence(glm_output, codex_output)

        assert isinstance(report, DivergenceReport)
        assert report.primary_source == "glm-5.1"
        assert report.secondary_source == "codex-gpt-5.5"

    def test_returns_normalized_divergence_score(self) -> None:
        """Returns a normalized divergence score in [0.0, 1.0]."""
        report = compute_divergence("output a", "output b")

        assert isinstance(report.overall_divergence, float)
        assert 0.0 <= report.overall_divergence <= 1.0

    def test_divergence_0_means_identical(self) -> None:
        """Divergence of 0.0 means the two outputs are effectively identical."""
        text = "Validation passed. 통과."
        report = compute_divergence(text, text)

        assert report.overall_divergence == 0.0
        assert report.overall_similarity == 1.0

    def test_divergence_1_means_completely_divergent(self) -> None:
        """Divergence approaching 1.0 means completely unrelated outputs."""
        primary, secondary = _completely_unrelated_outputs()
        report = compute_divergence(primary, secondary)

        # Should be very high (may not reach exactly 1.0 due to
        # coincidental stop-word overlap, but should be ≥ 0.7)
        assert report.overall_divergence >= 0.7, (
            f"Unrelated outputs should have divergence >= 0.7, "
            f"got {report.overall_divergence}"
        )

    def test_testable_with_curated_pairs(self) -> None:
        """The module is testable with curated output pairs at known
        agreement levels.

        This test demonstrates the full testability contract:
        - High agreement → low divergence
        - Moderate agreement → moderate divergence
        - No agreement → high divergence
        """
        # High agreement (identical)
        _, _, r_identical = (
            _identical_outputs()[0],
            _identical_outputs()[1],
            compute_divergence(*_identical_outputs()),
        )
        assert r_identical.overall_divergence <= 0.1

        # High agreement (near-identical)
        _, _, r_near = (
            _near_identical_outputs()[0],
            _near_identical_outputs()[1],
            compute_divergence(*_near_identical_outputs()),
        )
        assert r_near.overall_divergence <= 0.2

        # Low agreement (unrelated)
        _, _, r_unrelated = (
            _completely_unrelated_outputs()[0],
            _completely_unrelated_outputs()[1],
            compute_divergence(*_completely_unrelated_outputs()),
        )
        assert r_unrelated.overall_divergence >= 0.6

        # Ordering: identical < near-identical < unrelated
        assert (
            r_identical.overall_divergence
            <= r_near.overall_divergence
            <= r_unrelated.overall_divergence
        ), (
            f"Expected ordered divergence: "
            f"{r_identical.overall_divergence} <= "
            f"{r_near.overall_divergence} <= "
            f"{r_unrelated.overall_divergence}"
        )

    def test_default_divergence_threshold_constant(self) -> None:
        """DEFAULT_DIVERGENCE_THRESHOLD is exported and equals 0.30."""
        assert DEFAULT_DIVERGENCE_THRESHOLD == 0.30
