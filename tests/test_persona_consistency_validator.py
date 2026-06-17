"""Tests for the persona consistency validator — Sub-AC 6.2a.

Coverage:
- Seven standard personas: art-director, content-director, tech-director,
  marketing-lead, validator, executor, coordinator
- All four validation dimensions: tone, vocabulary, constraints,
  forbidden patterns
- PersonaSpec construction, validation, and immutability
- ToneProfile construction and defaults
- Perfectly aligned responses (should pass)
- Violation cases: wrong tone, missing vocabulary, constraint breach,
  forbidden pattern present
- Edge cases: empty response, whitespace-only, non-English text,
  very long response, very short response
- get_standard_spec() for all seven roles
- PersonaConsistencyReport properties (violation_count, critical_violations,
  violations_by_category)
- Integration: validate_persona_consistency() as the main entry point
- Deterministic output: same inputs produce identical reports
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from src.persona_consistency_validator import (
    ConsistencyViolation,
    PersonaSpec,
    ToneProfile,
    get_standard_spec,
    make_art_director_spec,
    make_content_director_spec,
    make_coordinator_spec,
    make_executor_spec,
    make_marketing_lead_spec,
    make_tech_director_spec,
    make_validator_spec,
    validate_persona_consistency,
)

# ═════════════════════════════════════════════════════════════════════════
# Fixtures — persona specs
# ═════════════════════════════════════════════════════════════════════════


@pytest.fixture
def art_director() -> PersonaSpec:
    return make_art_director_spec()


@pytest.fixture
def content_director() -> PersonaSpec:
    return make_content_director_spec()


@pytest.fixture
def tech_director() -> PersonaSpec:
    return make_tech_director_spec()


@pytest.fixture
def marketing_lead() -> PersonaSpec:
    return make_marketing_lead_spec()


@pytest.fixture
def validator_spec() -> PersonaSpec:
    return make_validator_spec()


@pytest.fixture
def executor_spec() -> PersonaSpec:
    return make_executor_spec()


@pytest.fixture
def coordinator_spec() -> PersonaSpec:
    return make_coordinator_spec()


# ═════════════════════════════════════════════════════════════════════════
# Fixtures — sample responses
# ═════════════════════════════════════════════════════════════════════════


@pytest.fixture
def art_director_good_response() -> str:
    """A well-aligned Art Director opinion."""
    return (
        "I recommend a neon-noir visual direction with high-contrast "
        "silhouettes for the protagonist line. The color palette should "
        "draw from cyberpunk aesthetics — purple-cyan gradients with "
        "warm accent colors for key visual elements. The composition "
        "should emphasize vertical hierarchy with strong leading lines.\n\n"
        "For the character design, we should maintain consistency with "
        "our existing design system while pushing the art style toward "
        "a more mature aesthetic. The typography for any in-scene text "
        "should use our brand identity guidelines.\n\n"
        "This approach will require coordination with the content team "
        "for narrative alignment and the tech team for shader requirements. "
        "I recommend we proceed with concept art exploration before "
        "finalizing the palette."
    )


@pytest.fixture
def tech_director_good_response() -> str:
    """A well-aligned Tech Director opinion."""
    return (
        "I recommend a microservices architecture for the new platform, "
        "with each service deployed as containerized workloads on our "
        "existing cloud infrastructure. The API design should follow "
        "REST principles with OpenAPI 3.1 specifications.\n\n"
        "For the database layer, I propose PostgreSQL with read replicas "
        "for performance. We must implement comprehensive monitoring "
        "from day one — Prometheus for metrics and structured logging.\n\n"
        "Security considerations: all inter-service communication must be "
        "TLS-encrypted. Authentication should use OAuth 2.0 with JWT.\n\n"
        "This design requires coordination with the content team for "
        "their data model requirements and the art team for asset "
        "delivery pipelines. I recommend we implement the core services "
        "first, then iterate based on performance benchmarks."
    )


@pytest.fixture
def marketing_lead_good_response() -> str:
    """A well-aligned Marketing Lead opinion."""
    return (
        "I recommend a multi-channel marketing strategy targeting the "
        "18-34 demographic through SNS platforms. Our brand positioning "
        "should emphasize the unique art style as the key differentiator.\n\n"
        "The campaign should launch with a teaser phase on Instagram and "
        "TikTok, followed by a full reveal on YouTube. Based on our market "
        "research, the engagement metrics for similar content in this "
        "segment show strong growth potential.\n\n"
        "We will need to coordinate with the art team for campaign assets "
        "and the content team for the reveal timeline. I recommend we set "
        "a 60-day pre-launch window to build audience anticipation."
    )


@pytest.fixture
def content_director_good_response() -> str:
    """A well-aligned Content Director opinion."""
    return (
        "I recommend we develop a three-act narrative structure for this "
        "production, with each act building audience engagement through "
        "escalating tension and character development.\n\n"
        "The script should establish a strong protagonist arc in Act 1, "
        "introduce conflict in Act 2, and deliver a satisfying resolution "
        "in Act 3. Our storytelling approach should leverage the visual "
        "direction from the art team to enhance emotional beats.\n\n"
        "For production planning, I recommend we allocate two weeks per "
        "act for script review and storyboard alignment. This requires "
        "coordination with the art team for visual development and the "
        "tech team for any CG requirements. The creative direction should "
        "maintain consistency with our existing content strategy."
    )


@pytest.fixture
def validator_good_response() -> str:
    """A well-aligned Validator response."""
    return (
        "After thorough review, I validate the proposed visual direction "
        "with the following observations.\n\n"
        "Compliance check: The color palette proposal aligns with our "
        "design standards (v2.3). The typography guidelines are correctly "
        "referenced. Risk assessment: moderate — the neon-noir style may "
        "require additional shader development work that should be verified "
        "with the tech team.\n\n"
        "I recommend the following evidence be collected before final "
        "approval: (1) concept art samples, (2) tech feasibility report, "
        "(3) market research validation. The quality threshold of 0.85 "
        "is met for visual direction, but the technical feasibility "
        "criteria require further verification."
    )


@pytest.fixture
def executor_good_response() -> str:
    """A well-aligned Executor response."""
    return (
        "Based on the consensus, I will implement the following action "
        "items.\n\n"
        "1. Deploy the concept art generation pipeline with the approved "
        "parameters. 2. Build the automated asset delivery workflow for "
        "the art team. 3. Execute the CI/CD pipeline updates for the "
        "new shader compilation step.\n\n"
        "These tasks will be tracked in the project automation system. "
        "I recommend a 48-hour implementation window with rollback "
        "capability. Coordination with the tech team is required for "
        "deployment pipeline access."
    )


@pytest.fixture
def coordinator_good_response() -> str:
    """A well-aligned Coordinator response."""
    return (
        "Meeting summary — Round 2 consensus building.\n\n"
        "Agenda: Character visual identity direction.\n"
        "Participants: Art Director, Content Director, Tech Director, "
        "Marketing Lead.\n\n"
        "Key decisions: (1) Neon-noir palette approved with purple-cyan "
        "gradients. (2) Character design to follow existing design system. "
        "(3) Tech team to evaluate shader requirements.\n\n"
        "Action items: Art team — concept art exploration (48h). Tech team "
        "— shader feasibility report (72h). Marketing — audience research "
        "alignment (48h).\n\n"
        "Next steps: Round 3 convergence meeting scheduled. Priority: P1."
    )


@pytest.fixture
def role_confused_response() -> str:
    """A response that shows role identity confusion (should fail)."""
    return (
        "I'm not sure what my role is here. As an AI language model, "
        "I can try to help with this visual design question, but I'm "
        "just an assistant. Maybe the art director should handle this? "
        "I don't know what team I'm on."
    )


@pytest.fixture
def forbidden_engineering_response() -> str:
    """An art director response making engineering decisions (should fail)."""
    return (
        "I think the art direction should use a microservices architecture "
        "and the database should be MongoDB. We should deploy everything "
        "on AWS with Kubernetes. The tech stack must include React and "
        "Node.js. We should refactor the entire backend."
    )


@pytest.fixture
def empty_response() -> str:
    return ""


@pytest.fixture
def whitespace_response() -> str:
    return "   \n  \t  \n  "


# ═════════════════════════════════════════════════════════════════════════
# 1. ToneProfile tests
# ═════════════════════════════════════════════════════════════════════════


class TestToneProfile:
    """Verify ToneProfile construction and defaults."""

    def test_default_construction(self) -> None:
        tp = ToneProfile()
        assert tp.formality == "professional"
        assert tp.assertiveness == "confident_measured"
        assert tp.emotional_valence == "neutral_positive"
        assert tp.style == "analytical"

    def test_custom_values(self) -> None:
        tp = ToneProfile(
            formality="casual",
            assertiveness="tentative",
            emotional_valence="enthusiastic",
            style="creative",
        )
        assert tp.formality == "casual"
        assert tp.assertiveness == "tentative"
        assert tp.emotional_valence == "enthusiastic"
        assert tp.style == "creative"

    def test_immutable(self) -> None:
        tp = ToneProfile()
        with pytest.raises(FrozenInstanceError):
            tp.formality = "casual"  # type: ignore[misc]


# ═════════════════════════════════════════════════════════════════════════
# 2. PersonaSpec tests
# ═════════════════════════════════════════════════════════════════════════


class TestPersonaSpec:
    """Verify PersonaSpec construction, validation, and immutability."""

    def test_minimal_construction(self) -> None:
        spec = PersonaSpec(
            role_id="test-role",
            display_name="Test Role",
            team="test-team",
        )
        assert spec.role_id == "test-role"
        assert spec.display_name == "Test Role"
        assert spec.team == "test-team"
        assert spec.role_type == "worker"
        assert spec.behavioral_constraints == ()
        assert spec.forbidden_patterns == ()

    def test_full_construction(self) -> None:
        spec = PersonaSpec(
            role_id="art-director",
            display_name="아트 디렉터",
            team="art-design",
            role_type="leader",
            tone_profile=ToneProfile(formality="professional"),
            role_vocabulary={
                "keywords": ["visual", "design"],
                "domain_terms": ["color", "palette"],
            },
            behavioral_constraints=(
                "stay_within_visual_design_domain",
                "acknowledge_cross_team_dependencies",
            ),
            forbidden_patterns=(
                "make_engineering_decisions",
                "make_financial_decisions",
            ),
        )
        assert spec.role_id == "art-director"
        assert spec.role_type == "leader"
        assert len(spec.behavioral_constraints) == 2
        assert len(spec.forbidden_patterns) == 2
        assert spec.role_vocabulary["keywords"] == ["visual", "design"]

    def test_empty_role_id_raises(self) -> None:
        with pytest.raises(ValueError, match="role_id must be"):
            PersonaSpec(role_id="", display_name="Test", team="test")

    def test_empty_display_name_raises(self) -> None:
        with pytest.raises(ValueError, match="display_name must be"):
            PersonaSpec(role_id="test", display_name="", team="test")

    def test_empty_team_raises(self) -> None:
        with pytest.raises(ValueError, match="team must be"):
            PersonaSpec(role_id="test", display_name="Test", team="")

    def test_immutable(self) -> None:
        spec = PersonaSpec(role_id="test", display_name="Test", team="test")
        with pytest.raises(FrozenInstanceError):
            spec.role_id = "changed"  # type: ignore[misc]

    def test_to_dict(self) -> None:
        spec = make_art_director_spec()
        d = spec.to_dict()
        assert d["role_id"] == "art-director"
        assert d["team"] == "art-design"
        assert isinstance(d["tone_profile"], dict)
        assert isinstance(d["behavioral_constraints"], list)


# ═════════════════════════════════════════════════════════════════════════
# 3. Standard persona spec factories
# ═════════════════════════════════════════════════════════════════════════


class TestStandardSpecs:
    """Verify all seven standard persona spec factories."""

    _all_factories = [
        make_art_director_spec,
        make_content_director_spec,
        make_tech_director_spec,
        make_marketing_lead_spec,
        make_validator_spec,
        make_executor_spec,
        make_coordinator_spec,
    ]

    _expected_ids = [
        "art-director",
        "content-director",
        "tech-director",
        "marketing-lead",
        "validator",
        "executor",
        "coordinator",
    ]

    def test_all_produce_valid_specs(self) -> None:
        """Every factory should produce a valid PersonaSpec."""
        for factory in self._all_factories:
            spec = factory()
            assert isinstance(spec, PersonaSpec)
            assert spec.role_id
            assert spec.display_name
            assert spec.team
            assert spec.role_type in ("leader", "worker", "validator", "executor")
            assert isinstance(spec.tone_profile, ToneProfile)

    def test_all_have_role_vocabulary(self) -> None:
        """Every standard spec should have vocabulary defined."""
        for factory in self._all_factories:
            spec = factory()
            assert "keywords" in spec.role_vocabulary, (
                f"{spec.role_id} missing keywords"
            )
            assert len(spec.role_vocabulary["keywords"]) > 0, (
                f"{spec.role_id} has empty keywords"
            )

    def test_all_have_behavioral_constraints(self) -> None:
        """Every leader spec should have behavioral constraints."""
        for factory, expected_id in zip(
            self._all_factories, self._expected_ids, strict=True
        ):
            spec = factory()
            assert len(spec.behavioral_constraints) > 0, (
                f"{expected_id} has no behavioral constraints"
            )

    def test_all_have_forbidden_patterns(self) -> None:
        """Every spec should have forbidden patterns."""
        for factory, expected_id in zip(
            self._all_factories, self._expected_ids, strict=True
        ):
            spec = factory()
            assert len(spec.forbidden_patterns) > 0, (
                f"{expected_id} has no forbidden patterns"
            )

    def test_get_standard_spec_all_roles(self) -> None:
        """get_standard_spec() should work for all seven roles."""
        for role_id in self._expected_ids:
            spec = get_standard_spec(role_id)
            assert spec.role_id == role_id

    def test_get_standard_spec_unknown_raises(self) -> None:
        with pytest.raises(KeyError, match="Unknown standard role_id"):
            get_standard_spec("nonexistent-role")


# ═════════════════════════════════════════════════════════════════════════
# 4. Validator — perfectly aligned responses (all 7 personas)
# ═════════════════════════════════════════════════════════════════════════


class TestPerfectAlignment:
    """Responses that are well-aligned with their persona should pass."""

    def test_art_director_passes(
        self,
        art_director: PersonaSpec,
        art_director_good_response: str,
    ) -> None:
        result = validate_persona_consistency(
            art_director_good_response, art_director
        )
        assert result.passed, (
            f"Art director should pass. Violations: {result.violations}"
        )
        assert result.overall_score >= 0.70

    def test_content_director_passes(
        self,
        content_director: PersonaSpec,
        content_director_good_response: str,
    ) -> None:
        result = validate_persona_consistency(
            content_director_good_response, content_director
        )
        assert result.passed, (
            f"Content director should pass. Violations: {result.violations}"
        )
        assert result.overall_score >= 0.70

    def test_tech_director_passes(
        self,
        tech_director: PersonaSpec,
        tech_director_good_response: str,
    ) -> None:
        result = validate_persona_consistency(
            tech_director_good_response, tech_director
        )
        assert result.passed, (
            f"Tech director should pass. Violations: {result.violations}"
        )
        assert result.overall_score >= 0.70

    def test_marketing_lead_passes(
        self,
        marketing_lead: PersonaSpec,
        marketing_lead_good_response: str,
    ) -> None:
        result = validate_persona_consistency(
            marketing_lead_good_response, marketing_lead
        )
        assert result.passed, (
            f"Marketing lead should pass. Violations: {result.violations}"
        )
        assert result.overall_score >= 0.70

    def test_validator_passes(
        self,
        validator_spec: PersonaSpec,
        validator_good_response: str,
    ) -> None:
        result = validate_persona_consistency(
            validator_good_response, validator_spec
        )
        assert result.passed, (
            f"Validator should pass. Violations: {result.violations}"
        )
        assert result.overall_score >= 0.70

    def test_executor_passes(
        self,
        executor_spec: PersonaSpec,
        executor_good_response: str,
    ) -> None:
        result = validate_persona_consistency(
            executor_good_response, executor_spec
        )
        assert result.passed, (
            f"Executor should pass. Violations: {result.violations}"
        )
        assert result.overall_score >= 0.70

    def test_coordinator_passes(
        self,
        coordinator_spec: PersonaSpec,
        coordinator_good_response: str,
    ) -> None:
        result = validate_persona_consistency(
            coordinator_good_response, coordinator_spec
        )
        assert result.passed, (
            f"Coordinator should pass. Violations: {result.violations}"
        )
        assert result.overall_score >= 0.70


# ═════════════════════════════════════════════════════════════════════════
# 5. Tone validation tests
# ═════════════════════════════════════════════════════════════════════════


class TestToneValidation:
    """Verify tone heuristics catch misaligned tone."""

    def test_hedging_language_penalizes_authoritative(
        self, art_director: PersonaSpec
    ) -> None:
        """A response full of hedging should score lower for authoritative."""
        hedging_response = (
            "Maybe we could perhaps consider a visual direction, "
            "if that's okay. I'm not entirely sure but maybe the palette "
            "could possibly be something like neon, perhaps? "
            "It might work, I think. But also maybe not."
        )
        result = validate_persona_consistency(hedging_response, art_director)
        assert result.tone_score < 1.0, "Hedging should reduce tone score"
        assert any(v.category == "tone" for v in result.violations)

    def test_negative_tone_penalizes_neutral_positive(
        self, art_director: PersonaSpec
    ) -> None:
        """Overly negative tone should flag for neutral_positive persona."""
        negative_response = (
            "This is terrible. The design is awful and disastrous. "
            "I am deeply worried about this direction. The outcome "
            "will be a complete failure. This is very bad."
        )
        result = validate_persona_consistency(negative_response, art_director)
        assert result.tone_score < 1.0

    def test_very_short_response_warns(self, art_director: PersonaSpec) -> None:
        result = validate_persona_consistency("OK, looks good.", art_director)
        assert len(result.violations) > 0
        assert any(
            "very short" in v.message.lower() for v in result.violations
        )


# ═════════════════════════════════════════════════════════════════════════
# 6. Vocabulary validation tests
# ═════════════════════════════════════════════════════════════════════════


class TestVocabularyValidation:
    """Verify vocabulary heuristics detect missing domain terms."""

    def test_no_domain_vocabulary_fails(
        self, art_director: PersonaSpec
    ) -> None:
        """Response with no art/design vocabulary should fail."""
        generic_response = (
            "I think this is a good idea. We should do it. "
            "Let's move forward with this plan. It seems fine. "
            "I agree with the general approach."
        )
        result = validate_persona_consistency(generic_response, art_director)
        assert result.vocabulary_score < 0.80
        assert any(v.category == "vocabulary" for v in result.violations)

    def test_leader_without_leadership_language(
        self, art_director: PersonaSpec
    ) -> None:
        """Leader persona should use leadership-oriented language."""
        non_leader_response = (
            "The visual direction looks nice. The palette is pretty. "
            "I like the colors. Good work everyone."
        )
        result = validate_persona_consistency(non_leader_response, art_director)
        assert result.vocabulary_score < 1.0

    def test_wrong_domain_vocabulary(
        self, tech_director: PersonaSpec
    ) -> None:
        """Response full of art terms from tech director should score lower."""
        art_heavy_response = (
            "The visual palette should use warm colors. The composition "
            "needs better typography. The character design is lovely. "
            "The aesthetic is beautiful. The illustration style is perfect. "
            "The graphic design elements need adjustment."
        )
        result = validate_persona_consistency(art_heavy_response, tech_director)
        assert result.vocabulary_score < 0.80


# ═════════════════════════════════════════════════════════════════════════
# 7. Constraint validation tests
# ═════════════════════════════════════════════════════════════════════════


class TestConstraintValidation:
    """Verify behavioral constraint checking."""

    def test_no_actionable_recommendations(
        self, art_director: PersonaSpec
    ) -> None:
        """Response without actionable items should flag."""
        vague_response = (
            "Visual direction is important. Design matters. "
            "We need good aesthetics. That is all."
        )
        result = validate_persona_consistency(vague_response, art_director)
        assert result.constraints_score < 1.0
        assert any(
            v.category == "constraint" for v in result.violations
        )

    def test_no_dependency_acknowledgment(
        self, tech_director: PersonaSpec
    ) -> None:
        """Response without cross-team dependency acknowledgment."""
        solo_response = (
            "We will build the entire system ourselves. No other "
            "teams are needed. The architecture is purely technical "
            "and requires no input from anyone else."
        )
        result = validate_persona_consistency(solo_response, tech_director)
        assert result.constraints_score < 1.0

    def test_override_authority_detected(
        self, art_director: PersonaSpec
    ) -> None:
        """Authority override language should be caught."""
        override_response = (
            "I will override the tech director's decision on this. "
            "I am going to decide for the marketing team what their "
            "strategy should be. I veto the content team's direction."
        )
        result = validate_persona_consistency(override_response, art_director)
        assert result.constraints_score < 0.80

    def test_no_constraints_all_pass(self) -> None:
        """Spec with no constraints should score 1.0."""
        spec = PersonaSpec(
            role_id="test",
            display_name="Test",
            team="test-team",
            behavioral_constraints=(),
        )
        result = validate_persona_consistency("Some response text.", spec)
        assert result.constraints_score == 1.0

    def test_unknown_constraint_warns(self) -> None:
        """Unknown constraint ID should warn but not crash."""
        spec = PersonaSpec(
            role_id="test",
            display_name="Test",
            team="test-team",
            behavioral_constraints=("nonexistent_constraint_xyz",),
        )
        result = validate_persona_consistency("Some response.", spec)
        assert any(
            "Unknown constraint" in v.message for v in result.violations
        )


# ═════════════════════════════════════════════════════════════════════════
# 8. Forbidden pattern tests
# ═════════════════════════════════════════════════════════════════════════


class TestForbiddenPatterns:
    """Verify forbidden pattern detection."""

    def test_identity_confusion_detected(
        self, art_director: PersonaSpec
    ) -> None:
        """Identity confusion should always be caught — critical."""
        result = validate_persona_consistency(
            "As an AI language model, I cannot make design decisions. "
            "I am just an assistant.",
            art_director,
        )
        assert result.forbidden_score < 1.0
        assert any(
            v.severity == "critical" and v.category == "forbidden_pattern"
            for v in result.violations
        )
        # Critical violations should cause overall failure
        assert not result.passed

    def test_engineering_decisions_flagged(
        self, art_director: PersonaSpec
    ) -> None:
        """Art director making engineering decisions should be caught."""
        result = validate_persona_consistency(
            "The architecture should use microservices and the database "
            "must be MongoDB. We should deploy on Kubernetes.",
            art_director,
        )
        assert result.forbidden_score < 1.0
        assert any(
            "make_engineering_decisions" in v.message
            for v in result.violations
        )

    def test_financial_decisions_flagged(
        self, art_director: PersonaSpec
    ) -> None:
        """Financial decisions should be caught."""
        result = validate_persona_consistency(
            "The budget should be $5 million and we should allocate "
            "funds to this project immediately.",
            art_director,
        )
        assert any(
            "make_financial_decisions" in v.message
            for v in result.violations
        )

    def test_marketing_override_flagged(
        self, art_director: PersonaSpec
    ) -> None:
        """Marketing strategy override should be caught."""
        result = validate_persona_consistency(
            "The marketing strategy should change to target seniors "
            "instead. The campaign must pivot to traditional media.",
            art_director,
        )
        assert any(
            "override_marketing_strategy" in v.message
            for v in result.violations
        )

    def test_role_abdication_flagged(
        self, validator_spec: PersonaSpec
    ) -> None:
        """Role abdication should be caught."""
        result = validate_persona_consistency(
            "I cannot make this decision. I defer completely to "
            "whatever the team wants. I won't provide any recommendation.",
            validator_spec,
        )
        assert any(
            "role_abdication" in v.message
            for v in result.violations
        )

    def test_personal_pronouns_warned(
        self, art_director: PersonaSpec
    ) -> None:
        """Inappropriate personal pronoun usage should be flagged."""
        # Create a spec that explicitly includes personal pronoun pattern
        spec = PersonaSpec(
            role_id=art_director.role_id,
            display_name=art_director.display_name,
            team=art_director.team,
            role_type=art_director.role_type,
            tone_profile=art_director.tone_profile,
            role_vocabulary=art_director.role_vocabulary,
            behavioral_constraints=art_director.behavioral_constraints,
            forbidden_patterns=(
                *art_director.forbidden_patterns,
                "use_personal_pronouns_inappropriately",
            ),
        )
        result = validate_persona_consistency(
            "In my personal opinion, I think the design should be blue. "
            "Personally, I believe the palette is wrong.",
            spec,
        )
        assert any(
            "inappropriately" in v.message
            for v in result.violations
        )

    def test_no_forbidden_patterns_passes(
        self, art_director: PersonaSpec,
        art_director_good_response: str,
    ) -> None:
        """Clean response with no forbidden patterns scores 1.0."""
        # Create spec with no explicit forbidden patterns
        spec = PersonaSpec(
            role_id="test",
            display_name="Test",
            team="test-team",
            forbidden_patterns=(),
        )
        result = validate_persona_consistency(
            "A normal response without any concerning patterns.", spec
        )
        assert result.forbidden_score == 1.0

    def test_art_director_contradiction_flagged(
        self, tech_director: PersonaSpec
    ) -> None:
        """Contradicting art director authority should be caught."""
        # Create a spec that explicitly includes authority contradiction pattern
        spec = PersonaSpec(
            role_id=tech_director.role_id,
            display_name=tech_director.display_name,
            team=tech_director.team,
            role_type=tech_director.role_type,
            tone_profile=tech_director.tone_profile,
            role_vocabulary=tech_director.role_vocabulary,
            behavioral_constraints=tech_director.behavioral_constraints,
            forbidden_patterns=(
                *tech_director.forbidden_patterns,
                "contradict_art_director_authority",
            ),
        )
        result = validate_persona_consistency(
            "The art director is wrong about the visual direction. "
            "Their art style decision is incorrect.",
            spec,
        )
        assert any(
            "contradict_art_director_authority" in v.message
            for v in result.violations
        )


# ═════════════════════════════════════════════════════════════════════════
# 9. Edge cases
# ═════════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    """Verify edge case handling."""

    def test_empty_response_fails(
        self, art_director: PersonaSpec, empty_response: str
    ) -> None:
        result = validate_persona_consistency(empty_response, art_director)
        assert not result.passed
        assert result.overall_score == 0.0
        assert result.response_length == 0

    def test_whitespace_response_fails(
        self, art_director: PersonaSpec, whitespace_response: str
    ) -> None:
        result = validate_persona_consistency(whitespace_response, art_director)
        assert not result.passed
        assert result.overall_score == 0.0

    def test_custom_threshold(self, art_director: PersonaSpec) -> None:
        """Custom threshold should affect pass/fail."""
        response = "A somewhat short but acceptable design recommendation."
        # With a low threshold, this should pass
        result_low = validate_persona_consistency(
            response, art_director, threshold=0.40
        )
        # With a high threshold, same response should fail
        result_high = validate_persona_consistency(
            response, art_director, threshold=0.95
        )
        assert result_low.passed or result_high.passed != result_low.passed

    def test_very_long_response_handled(
        self, art_director: PersonaSpec
    ) -> None:
        """Very long responses should not crash."""
        long_text = (
            "I recommend this visual direction. "
            + "The design system should incorporate many elements. " * 50
        )
        result = validate_persona_consistency(long_text, art_director)
        assert result.response_length > 2500
        assert isinstance(result.overall_score, float)

    def test_non_english_response_handled(
        self, content_director: PersonaSpec
    ) -> None:
        """Korean response should be handled by Korean heuristics."""
        korean_response = (
            "이번 프로젝트의 스토리텔링 방향에 대해 말씀드리겠습니다. "
            "3막 구조로 진행하는 것을 추천드립니다. "
            "각 막마다 캐릭터 발전을 강조해야 합니다. "
            "대본 작업은 2주 안에 완료하는 것이 좋겠습니다."
        )
        result = validate_persona_consistency(
            korean_response, content_director
        )
        assert result.overall_score > 0.0
        assert isinstance(result.tone_score, float)

    def test_non_dict_vocab_handled(self) -> None:
        """Empty role_vocabulary should not crash."""
        spec = PersonaSpec(
            role_id="test",
            display_name="Test",
            team="test-team",
            role_vocabulary={},
        )
        result = validate_persona_consistency("Some response.", spec)
        assert result.vocabulary_score >= 0.0


# ═════════════════════════════════════════════════════════════════════════
# 10. Report properties
# ═════════════════════════════════════════════════════════════════════════


class TestReportProperties:
    """Verify PersonaConsistencyReport properties."""

    def test_violation_count(self, art_director: PersonaSpec) -> None:
        result = validate_persona_consistency("", art_director)
        assert result.violation_count == 1

    def test_critical_violations(self, art_director: PersonaSpec) -> None:
        result = validate_persona_consistency(
            "As an AI language model, I'm just an assistant.",
            art_director,
        )
        assert result.critical_violations >= 1

    def test_violations_by_category(self, art_director: PersonaSpec) -> None:
        bad_response = (
            "As an AI, I am just an assistant. The architecture should "
            "be microservices with MongoDB. The budget is $10 million. "
            "The marketing strategy must pivot."
        )
        result = validate_persona_consistency(bad_response, art_director)
        by_cat = result.violations_by_category()
        assert isinstance(by_cat, dict)
        assert "forbidden_pattern" in by_cat

    def test_no_violations_clean_response(
        self,
        art_director: PersonaSpec,
        art_director_good_response: str,
    ) -> None:
        result = validate_persona_consistency(
            art_director_good_response, art_director
        )
        # May have minor warnings, but no critical/major issues
        assert result.critical_violations == 0

    def test_persona_id_in_report(self, art_director: PersonaSpec) -> None:
        result = validate_persona_consistency("test", art_director)
        assert result.persona_id == "art-director"

    def test_threshold_stored(self, art_director: PersonaSpec) -> None:
        result = validate_persona_consistency(
            "test", art_director, threshold=0.85
        )
        assert result.threshold == 0.85


# ═════════════════════════════════════════════════════════════════════════
# 11. Determinism
# ═════════════════════════════════════════════════════════════════════════


class TestDeterminism:
    """Same inputs must produce identical reports."""

    def test_same_inputs_same_output(
        self,
        art_director: PersonaSpec,
        art_director_good_response: str,
    ) -> None:
        r1 = validate_persona_consistency(art_director_good_response, art_director)
        r2 = validate_persona_consistency(art_director_good_response, art_director)
        assert r1.overall_score == r2.overall_score
        assert r1.tone_score == r2.tone_score
        assert r1.vocabulary_score == r2.vocabulary_score
        assert r1.constraints_score == r2.constraints_score
        assert r1.forbidden_score == r2.forbidden_score
        assert r1.passed == r2.passed
        assert r1.violation_count == r2.violation_count

    def test_different_personas_different_results(
        self,
        art_director: PersonaSpec,
        tech_director: PersonaSpec,
        art_director_good_response: str,
    ) -> None:
        """Same response against different personas should differ."""
        r1 = validate_persona_consistency(art_director_good_response, art_director)
        r2 = validate_persona_consistency(art_director_good_response, tech_director)
        # At least vocabulary score should differ (art vs tech domains)
        assert r1.vocabulary_score != r2.vocabulary_score, (
            f"Expected different vocabulary scores: "
            f"{r1.vocabulary_score} vs {r2.vocabulary_score}"
        )


# ═════════════════════════════════════════════════════════════════════════
# 12. ConsistencyViolation dataclass
# ═════════════════════════════════════════════════════════════════════════


class TestConsistencyViolation:
    """Verify ConsistencyViolation dataclass."""

    def test_construction(self) -> None:
        v = ConsistencyViolation(
            category="tone",
            severity="major",
            message="Test violation",
            detail="Some detail",
        )
        assert v.category == "tone"
        assert v.severity == "major"
        assert v.message == "Test violation"
        assert v.detail == "Some detail"

    def test_default_detail(self) -> None:
        v = ConsistencyViolation(
            category="vocabulary",
            severity="warning",
            message="Missing terms",
        )
        assert v.detail == ""

    def test_immutable(self) -> None:
        v = ConsistencyViolation(
            category="tone",
            severity="major",
            message="Test",
        )
        with pytest.raises(FrozenInstanceError):
            v.severity = "minor"  # type: ignore[misc]

    def test_equality(self) -> None:
        v1 = ConsistencyViolation("tone", "major", "msg", "detail")
        v2 = ConsistencyViolation("tone", "major", "msg", "detail")
        assert v1 == v2

    def test_inequality(self) -> None:
        v1 = ConsistencyViolation("tone", "major", "msg1")
        v2 = ConsistencyViolation("tone", "major", "msg2")
        assert v1 != v2
