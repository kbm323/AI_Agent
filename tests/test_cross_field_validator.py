"""Comprehensive tests for the cross-field data integrity and consistency validator.

Sub-AC 6.1.3: Cross-field data integrity and consistency validation —
verifies logical consistency across fields (valid references, coherent
round-session metadata, matching participant counts); testable with
fuzzed data, inconsistent cross-field states, and corrupted references.

Test coverage:
- Valid manifest baseline (all rules pass)
- Speaker not in role lists (reference integrity)
- Speaker queue entries not in role lists
- Context packet role IDs not in role lists
- Decision role IDs not in role lists
- Tool output role IDs not in role lists
- Role ID not valid kebab-case in sub-collections
- round_count lower than max round in sub-collections
- Sub-collection round exceeds max_rounds + 1
- Participant count exceeds max_agents_per_meeting
- Validation verdict/score inconsistency
- Completed state with pass verdict but score < 0.85
- State logic: completed without rounds or consensus
- State logic: failed/escalated/deadlocked without errors
- State logic: in_meeting without agenda_type
- Timestamp ordering violation
- Risk tags without validator_required
- Codex-trigger risk tags without codex_required
- Token limit hierarchy violations
- None and non-dict input
- Fuzzed data (corrupted references, inconsistent states)
- Empty role lists with speaker set
- Boundary conditions (exactly at limits)
- Multiple simultaneous errors
- CrossFieldReport properties (error_count, warning_count, errors_by_category)
"""

from __future__ import annotations

from src.cross_field_validator import (
    _RULES,
    CrossFieldReport,
    validate_cross_field_integrity,
)

# ═══════════════════════════════════════════════════════════════════════
# Helper: build a fully valid manifest dict
# ═══════════════════════════════════════════════════════════════════════

def _valid_manifest(**overrides: object) -> dict[str, object]:
    """Return a fully valid meeting manifest dict with all cross-field
    rules satisfied."""
    defaults: dict[str, object] = {
        "meeting_id": "meeting_20260610_5a36918413b1",
        "state": "created",
        "priority": "p2",
        "agenda": "Music video opening ideas",
        "agenda_type": "",
        "tags": ["mv", "visual-concept"],
        "risk_tags": [],
        "required_roles": ["coordinator", "art-director", "producer-kim"],
        "optional_roles": ["concept-artist", "sns-strategist"],
        "round_count": 2,
        "validation_score": 0.0,
        "validation_verdict": "",
        "validator_required": True,
        "codex_required": False,
        "consensus": "",
        "user_id": "u1",
        "channel_id": "c1",
        "thread_id": "",
        "guild_id": "",
        "error_log": [],
        "manifest_path": "/home/kbm/F:ai-projects/AI_Agent/meetings/m1/manifest.json",
        "meetings_root": "meetings",
        "max_rounds": 3,
        "max_agents_per_meeting": 7,
        "token_limit_worker": 12000,
        "token_limit_validator": 20000,
        "token_limit_codex": 30000,
        "primary_validator_model": "glm-5.1",
        "conditional_validator_model": "gpt-5.5",
        "schema_version": "meeting-manifest.v1",
        "current_speaker": "",
        "speaker_queue": [],
        "completed_step": "",
        "context_packets": [
            {
                "round": 1,
                "role_id": "art-director",
                "model_provider": "qwen",
                "model_name": "qwen3-max",
                "token_count": 8500,
                "packet_path": "round_1/art-director.json",
                "opinion_summary": "Neon-noir palette for MV opening",
                "created_at": "2026-06-10T14:30:00Z",
            },
            {
                "round": 2,
                "role_id": "producer-kim",
                "model_provider": "deepseek",
                "model_name": "deepseek-v4",
                "token_count": 9200,
                "packet_path": "round_2/producer-kim.json",
                "opinion_summary": "Budget approval for concept",
                "created_at": "2026-06-10T14:35:00Z",
            },
        ],
        "decisions": [
            {
                "round": 1,
                "decision_id": "d_001",
                "role_id": "art-director",
                "content": "Adopt neon-noir palette",
                "superseded_by": "",
                "created_at": "2026-06-10T14:31:00Z",
            },
        ],
        "tool_outputs": [],
        "created_at": "2026-06-10T14:20:00Z",
        "updated_at": "2026-06-10T14:35:00Z",
    }
    defaults.update(overrides)
    return defaults


# ═══════════════════════════════════════════════════════════════════════
# 1. Valid manifest — baseline pass
# ═══════════════════════════════════════════════════════════════════════

class TestValidManifest:
    """Verify that a fully correct manifest passes all cross-field rules."""

    def test_fully_valid_manifest_passes(self) -> None:
        report = validate_cross_field_integrity(_valid_manifest())
        assert report.passed
        assert report.error_count == 0
        assert report.warning_count == 0
        assert report.rule_count == len(_RULES)
        assert report.schema_version == "cross-field-validation.v1"

    def test_manifest_with_completed_state_passes(self) -> None:
        """A properly completed manifest should pass."""
        m = _valid_manifest(
            state="completed",
            round_count=2,
            validation_score=0.92,
            validation_verdict="pass",
            consensus="Meeting concluded successfully with neon-noir decision.",
        )
        report = validate_cross_field_integrity(m)
        assert report.passed
        # consensus warning is advisory only
        assert all(
            e.severity == "warning" for e in report.errors
            if e.severity != "error"
        )

    def test_manifest_with_all_roles_used_passes(self) -> None:
        """All roles in packets appear in required_roles or optional_roles."""
        m = _valid_manifest(
            current_speaker="art-director",
            speaker_queue=["art-director", "producer-kim"],
        )
        report = validate_cross_field_integrity(m)
        assert report.passed

    def test_empty_manifest_baseline_passes(self) -> None:
        """An empty/initial manifest (no sub-collections) passes."""
        m = _valid_manifest(
            context_packets=[],
            decisions=[],
            tool_outputs=[],
            round_count=0,
            current_speaker="",
            speaker_queue=[],
        )
        report = validate_cross_field_integrity(m)
        assert report.passed


# ═══════════════════════════════════════════════════════════════════════
# 2. Reference integrity — speaker not in roles
# ═══════════════════════════════════════════════════════════════════════

class TestSpeakerInRoles:
    """Verify current_speaker and speaker_queue reference integrity."""

    def test_speaker_not_in_roles_detected(self) -> None:
        m = _valid_manifest(current_speaker="ghost-role")
        report = validate_cross_field_integrity(m)
        assert not report.passed
        err = next(
            e for e in report.errors if e.rule_id == "speaker_not_in_roles"
        )
        assert err.category == "reference_integrity"
        assert err.severity == "error"
        assert "ghost-role" in err.message

    def test_speaker_queue_entry_not_in_roles(self) -> None:
        m = _valid_manifest(
            speaker_queue=["art-director", "invalid-role", "concept-artist"]
        )
        report = validate_cross_field_integrity(m)
        assert not report.passed
        errs = [
            e for e in report.errors
            if e.rule_id == "speaker_queue_entry_not_in_roles"
        ]
        assert len(errs) >= 1
        assert any("invalid-role" in e.message for e in errs)

    def test_speaker_queue_all_valid_passes(self) -> None:
        m = _valid_manifest(
            speaker_queue=["art-director", "producer-kim", "concept-artist"]
        )
        report = validate_cross_field_integrity(m)
        # No speaker_queue errors
        queue_errs = [
            e for e in report.errors
            if e.rule_id == "speaker_queue_entry_not_in_roles"
        ]
        assert len(queue_errs) == 0

    def test_empty_speaker_and_queue_passes(self) -> None:
        m = _valid_manifest(current_speaker="", speaker_queue=[])
        report = validate_cross_field_integrity(m)
        assert report.passed


# ═══════════════════════════════════════════════════════════════════════
# 3. Reference integrity — packet/decision/output roles
# ═══════════════════════════════════════════════════════════════════════

class TestSubCollectionRoleReferences:
    """Verify role_id references in context_packets, decisions, tool_outputs."""

    def test_packet_role_not_in_roles(self) -> None:
        m = _valid_manifest(context_packets=[
            {"round": 1, "role_id": "alien-role", "created_at": "2026-06-10T14:30:00Z"},
        ])
        report = validate_cross_field_integrity(m)
        assert not report.passed
        err = next(
            e for e in report.errors
            if e.rule_id == "packet_role_not_in_roles"
        )
        assert "alien-role" in err.message

    def test_decision_role_not_in_roles(self) -> None:
        m = _valid_manifest(decisions=[
            {"round": 1, "decision_id": "d1", "role_id": "ghost-director",
             "content": "test", "superseded_by": "",
             "created_at": "2026-06-10T14:30:00Z"},
        ])
        report = validate_cross_field_integrity(m)
        assert not report.passed
        err = next(
            e for e in report.errors
            if e.rule_id == "decision_role_not_in_roles"
        )
        assert "ghost-director" in err.message

    def test_tool_output_role_not_in_roles(self) -> None:
        m = _valid_manifest(tool_outputs=[
            {"round": 1, "execution_id": "exec1", "action_type": "deploy",
             "role_id": "secret-agent", "status": "success", "output": "",
             "risk_level": "low", "human_approved": None,
             "created_at": "2026-06-10T14:30:00Z"},
        ])
        report = validate_cross_field_integrity(m)
        assert not report.passed
        err = next(
            e for e in report.errors
            if e.rule_id == "tool_output_role_not_in_roles"
        )
        assert "secret-agent" in err.message

    def test_optional_role_in_packets_passes(self) -> None:
        """Role IDs from optional_roles should be accepted in packets."""
        m = _valid_manifest(context_packets=[
            {"round": 1, "role_id": "concept-artist",
             "created_at": "2026-06-10T14:30:00Z"},
        ])
        report = validate_cross_field_integrity(m)
        assert report.passed


# ═══════════════════════════════════════════════════════════════════════
# 4. Role ID kebab-case validation in sub-collections
# ═══════════════════════════════════════════════════════════════════════

class TestRoleIdKebabCase:
    """Verify role_id values in sub-collections are valid kebab-case."""

    def test_packet_role_id_not_kebab(self) -> None:
        required = ["coordinator", "UPPERCASE-ROLE", "producer-kim"]
        m = _valid_manifest(
            required_roles=required,
            context_packets=[
                {"round": 1, "role_id": "UPPERCASE-ROLE",
                 "created_at": "2026-06-10T14:30:00Z"},
            ],
        )
        report = validate_cross_field_integrity(m)
        errs = [
            e for e in report.errors
            if e.rule_id == "packet_role_id_not_kebab"
        ]
        assert len(errs) >= 1

    def test_decision_role_id_not_kebab(self) -> None:
        required = ["coordinator", "Bad_Role", "producer-kim"]
        m = _valid_manifest(
            required_roles=required,
            decisions=[
                {"round": 1, "decision_id": "d1", "role_id": "Bad_Role",
                 "content": "test", "superseded_by": "",
                 "created_at": "2026-06-10T14:30:00Z"},
            ],
        )
        report = validate_cross_field_integrity(m)
        errs = [
            e for e in report.errors
            if e.rule_id == "decision_role_id_not_kebab"
        ]
        assert len(errs) >= 1

    def test_tool_output_role_id_not_kebab(self) -> None:
        required = ["coordinator", "role with spaces", "producer-kim"]
        m = _valid_manifest(
            required_roles=required,
            tool_outputs=[
                {"round": 1, "execution_id": "e1", "action_type": "test",
                 "role_id": "role with spaces", "status": "ok", "output": "",
                 "risk_level": "low", "human_approved": None,
                 "created_at": "2026-06-10T14:30:00Z"},
            ],
        )
        report = validate_cross_field_integrity(m)
        errs = [
            e for e in report.errors
            if e.rule_id == "tool_output_role_id_not_kebab"
        ]
        assert len(errs) >= 1


# ═══════════════════════════════════════════════════════════════════════
# 5. Round count coherence
# ═══════════════════════════════════════════════════════════════════════

class TestRoundCountCoherence:
    """Verify round_count consistency with sub-collections."""

    def test_round_count_too_low(self) -> None:
        m = _valid_manifest(
            round_count=1,
            context_packets=[
                {"round": 2, "role_id": "art-director",
                 "created_at": "2026-06-10T14:30:00Z"},
                {"round": 3, "role_id": "producer-kim",
                 "created_at": "2026-06-10T14:35:00Z"},
            ],
        )
        report = validate_cross_field_integrity(m)
        assert not report.passed
        err = next(
            e for e in report.errors
            if e.rule_id == "round_count_too_low"
        )
        assert "round_count=1" in err.message
        assert "3" in err.message

    def test_round_count_equal_to_max_passes(self) -> None:
        m = _valid_manifest(
            round_count=2,
            context_packets=[
                {"round": 2, "role_id": "art-director",
                 "created_at": "2026-06-10T14:30:00Z"},
            ],
        )
        report = validate_cross_field_integrity(m)
        assert report.passed

    def test_round_exceeds_max_plus_tie_break(self) -> None:
        """max_rounds=3, so max allowed is 4 (tie-break)."""
        m = _valid_manifest(
            max_rounds=3,
            context_packets=[
                {"round": 5, "role_id": "art-director",
                 "created_at": "2026-06-10T14:30:00Z"},
            ],
        )
        report = validate_cross_field_integrity(m)
        assert not report.passed
        err = next(
            e for e in report.errors
            if e.rule_id == "packet_round_exceeds_max"
        )
        assert "5" in err.message

    def test_tie_break_round_accepted(self) -> None:
        """Round 4 is the tie-break round with max_rounds=3 — should pass."""
        m = _valid_manifest(
            max_rounds=3,
            round_count=4,
            context_packets=[
                {"round": 4, "role_id": "art-director",
                 "created_at": "2026-06-10T14:30:00Z"},
            ],
        )
        report = validate_cross_field_integrity(m)
        # round 4 should pass (tie-break)
        exceeds_errs = [
            e for e in report.errors
            if e.rule_id == "packet_round_exceeds_max"
        ]
        assert len(exceeds_errs) == 0


# ═══════════════════════════════════════════════════════════════════════
# 6. Participant count limit
# ═══════════════════════════════════════════════════════════════════════

class TestParticipantCount:
    """Verify unique role IDs don't exceed max_agents_per_meeting."""

    def test_within_limit_passes(self) -> None:
        m = _valid_manifest(
            max_agents_per_meeting=3,
            required_roles=["a", "b", "c"],
            optional_roles=[],
            round_count=1,
            decisions=[],
            context_packets=[
                {"round": 1, "role_id": "a",
                 "created_at": "2026-06-10T14:30:00Z"},
                {"round": 1, "role_id": "b",
                 "created_at": "2026-06-10T14:30:00Z"},
                {"round": 1, "role_id": "c",
                 "created_at": "2026-06-10T14:30:00Z"},
            ],
        )
        report = validate_cross_field_integrity(m)
        assert report.passed

    def test_exceeds_limit_detected(self) -> None:
        m = _valid_manifest(
            max_agents_per_meeting=2,
            required_roles=["a", "b", "c", "d"],
            optional_roles=[],
            round_count=1,
            decisions=[],
            context_packets=[
                {"round": 1, "role_id": "a",
                 "created_at": "2026-06-10T14:30:00Z"},
                {"round": 1, "role_id": "b",
                 "created_at": "2026-06-10T14:30:00Z"},
                {"round": 1, "role_id": "c",
                 "created_at": "2026-06-10T14:30:00Z"},
            ],
        )
        report = validate_cross_field_integrity(m)
        assert not report.passed
        err = next(
            e for e in report.errors
            if e.rule_id == "participant_limit_exceeded"
        )
        assert "3" in err.message  # 3 unique roles
        assert "2" in err.message  # max 2

    def test_exactly_at_limit_passes(self) -> None:
        m = _valid_manifest(
            max_agents_per_meeting=2,
            required_roles=["a", "b"],
            optional_roles=[],
            round_count=1,
            decisions=[],
            context_packets=[
                {"round": 1, "role_id": "a",
                 "created_at": "2026-06-10T14:30:00Z"},
                {"round": 1, "role_id": "b",
                 "created_at": "2026-06-10T14:30:00Z"},
            ],
        )
        report = validate_cross_field_integrity(m)
        assert report.passed


# ═══════════════════════════════════════════════════════════════════════
# 7. Validation score/verdict consistency
# ═══════════════════════════════════════════════════════════════════════

class TestValidationSemantics:
    """Verify validation_score and validation_verdict consistency."""

    def test_verdict_set_but_score_invalid(self) -> None:
        m = _valid_manifest(
            validation_verdict="pass",
            validation_score=-1.0,
        )
        report = validate_cross_field_integrity(m)
        assert not report.passed
        err = next(
            e for e in report.errors
            if e.rule_id == "verdict_without_valid_score"
        )
        assert "pass" in err.message

    def test_verdict_set_but_score_is_string(self) -> None:
        m = _valid_manifest(
            validation_verdict="pass",
            validation_score="high",  # type: ignore
        )
        report = validate_cross_field_integrity(m)
        assert not report.passed
        err = next(
            e for e in report.errors
            if e.rule_id == "verdict_without_valid_score"
        )
        assert "high" in err.message

    def test_completed_pass_requires_high_score(self) -> None:
        m = _valid_manifest(
            state="completed",
            validation_verdict="pass",
            validation_score=0.72,
            round_count=2,
            consensus="Done.",
        )
        report = validate_cross_field_integrity(m)
        assert not report.passed
        err = next(
            e for e in report.errors
            if e.rule_id == "completed_pass_score_too_low"
        )
        assert "0.72" in err.message
        assert "0.85" in err.message

    def test_completed_pass_with_high_score_passes(self) -> None:
        m = _valid_manifest(
            state="completed",
            validation_verdict="pass",
            validation_score=0.92,
            round_count=2,
            consensus="Done.",
        )
        report = validate_cross_field_integrity(m)
        # Should pass error checks; consensus warning is fine
        assert report.error_count == 0

    def test_conditional_pass_with_low_score_passes(self) -> None:
        """conditional_pass doesn't require 0.85 — only 'pass' does."""
        m = _valid_manifest(
            state="completed",
            validation_verdict="conditional_pass",
            validation_score=0.72,
            round_count=2,
            consensus="Done with conditions.",
        )
        report = validate_cross_field_integrity(m)
        assert report.error_count == 0


# ═══════════════════════════════════════════════════════════════════════
# 8. State logic consistency
# ═══════════════════════════════════════════════════════════════════════

class TestStateLogic:
    """Verify state-specific consistency rules."""

    def test_completed_without_rounds_errors(self) -> None:
        m = _valid_manifest(
            state="completed",
            round_count=0,
            validation_score=0.92,
            validation_verdict="pass",
            consensus="Done.",
        )
        report = validate_cross_field_integrity(m)
        assert not report.passed
        err = next(
            e for e in report.errors
            if e.rule_id == "completed_without_rounds"
        )
        assert err.severity == "error"

    def test_completed_without_consensus_warns(self) -> None:
        m = _valid_manifest(
            state="completed",
            round_count=2,
            validation_score=0.92,
            validation_verdict="pass",
            consensus="",
        )
        report = validate_cross_field_integrity(m)
        # Warning, not error — should not cause passed=False
        warns = [
            e for e in report.errors
            if e.rule_id == "completed_without_consensus"
        ]
        assert len(warns) == 1
        assert warns[0].severity == "warning"

    def test_failed_without_error_log_warns(self) -> None:
        m = _valid_manifest(
            state="failed",
            error_log=[],
        )
        report = validate_cross_field_integrity(m)
        warns = [
            e for e in report.errors
            if e.rule_id == "terminal_state_without_errors"
        ]
        assert len(warns) == 1
        assert warns[0].severity == "warning"

    def test_escalated_without_error_log_warns(self) -> None:
        m = _valid_manifest(state="escalated", error_log=[])
        report = validate_cross_field_integrity(m)
        warns = [
            e for e in report.errors
            if e.rule_id == "terminal_state_without_errors"
        ]
        assert len(warns) == 1

    def test_deadlocked_without_error_log_warns(self) -> None:
        m = _valid_manifest(state="deadlocked", error_log=[])
        report = validate_cross_field_integrity(m)
        warns = [
            e for e in report.errors
            if e.rule_id == "terminal_state_without_errors"
        ]
        assert len(warns) == 1

    def test_in_meeting_without_agenda_type_warns(self) -> None:
        m = _valid_manifest(state="in_meeting", agenda_type="")
        report = validate_cross_field_integrity(m)
        warns = [
            e for e in report.errors
            if e.rule_id == "in_meeting_without_agenda_type"
        ]
        assert len(warns) == 1
        assert warns[0].severity == "warning"

    def test_in_meeting_with_agenda_type_passes(self) -> None:
        m = _valid_manifest(
            state="in_meeting",
            agenda_type="creative_production",
        )
        report = validate_cross_field_integrity(m)
        warns = [
            e for e in report.errors
            if e.rule_id == "in_meeting_without_agenda_type"
        ]
        assert len(warns) == 0


# ═══════════════════════════════════════════════════════════════════════
# 9. Timestamp ordering
# ═══════════════════════════════════════════════════════════════════════

class TestTimestampOrdering:
    """Verify created_at <= updated_at."""

    def test_created_before_updated_passes(self) -> None:
        m = _valid_manifest(
            created_at="2026-06-10T14:20:00Z",
            updated_at="2026-06-10T14:35:00Z",
        )
        report = validate_cross_field_integrity(m)
        assert report.passed

    def test_created_equal_updated_passes(self) -> None:
        m = _valid_manifest(
            created_at="2026-06-10T14:20:00Z",
            updated_at="2026-06-10T14:20:00Z",
        )
        report = validate_cross_field_integrity(m)
        assert report.passed

    def test_created_after_updated_detected(self) -> None:
        m = _valid_manifest(
            created_at="2026-06-10T15:00:00Z",
            updated_at="2026-06-10T14:00:00Z",
        )
        report = validate_cross_field_integrity(m)
        assert not report.passed
        err = next(
            e for e in report.errors
            if e.rule_id == "created_after_updated"
        )
        assert err.category == "timestamp_ordering"

    def test_unparseable_timestamps_skipped(self) -> None:
        """Unparseable timestamps are skipped (field-format validator handles)."""
        m = _valid_manifest(
            created_at="not-a-timestamp",
            updated_at="also-bad",
        )
        report = validate_cross_field_integrity(m)
        # Should not crash and should not produce timestamp errors
        ts_errs = [
            e for e in report.errors
            if e.rule_id == "created_after_updated"
        ]
        assert len(ts_errs) == 0

    def test_missing_timestamps_skipped(self) -> None:
        m = _valid_manifest(created_at="", updated_at="")
        report = validate_cross_field_integrity(m)
        ts_errs = [
            e for e in report.errors
            if e.rule_id == "created_after_updated"
        ]
        assert len(ts_errs) == 0


# ═══════════════════════════════════════════════════════════════════════
# 10. Risk-validator linkage
# ═══════════════════════════════════════════════════════════════════════

class TestRiskValidatorLinkage:
    """Verify risk_tags trigger validator_required and codex_required."""

    def test_risk_tags_require_validator(self) -> None:
        m = _valid_manifest(
            risk_tags=["brand"],
            validator_required=False,
        )
        report = validate_cross_field_integrity(m)
        assert not report.passed
        err = next(
            e for e in report.errors
            if e.rule_id == "risk_tags_without_validator"
        )
        assert "brand" in err.message

    def test_no_risk_tags_without_validator_passes(self) -> None:
        m = _valid_manifest(
            risk_tags=[],
            validator_required=False,
        )
        report = validate_cross_field_integrity(m)
        # No risk_tags error because risk_tags is empty
        risk_errs = [
            e for e in report.errors
            if e.rule_id == "risk_tags_without_validator"
        ]
        assert len(risk_errs) == 0

    def test_codex_trigger_tags_require_codex(self) -> None:
        m = _valid_manifest(
            risk_tags=["legal", "brand"],
            validator_required=True,
            codex_required=False,
        )
        report = validate_cross_field_integrity(m)
        warns = [
            e for e in report.errors
            if e.rule_id == "codex_trigger_without_codex"
        ]
        assert len(warns) == 1
        assert warns[0].severity == "warning"
        assert "legal" in warns[0].message

    def test_codex_trigger_with_codex_enabled_passes(self) -> None:
        m = _valid_manifest(
            risk_tags=["legal", "compliance"],
            validator_required=True,
            codex_required=True,
        )
        report = validate_cross_field_integrity(m)
        warns = [
            e for e in report.errors
            if e.rule_id == "codex_trigger_without_codex"
        ]
        assert len(warns) == 0

    def test_safety_triggers_codex(self) -> None:
        m = _valid_manifest(
            risk_tags=["safety"],
            validator_required=True,
            codex_required=False,
        )
        report = validate_cross_field_integrity(m)
        warns = [
            e for e in report.errors
            if e.rule_id == "codex_trigger_without_codex"
        ]
        assert len(warns) == 1

    def test_financial_triggers_codex(self) -> None:
        m = _valid_manifest(
            risk_tags=["financial"],
            validator_required=True,
            codex_required=False,
        )
        report = validate_cross_field_integrity(m)
        warns = [
            e for e in report.errors
            if e.rule_id == "codex_trigger_without_codex"
        ]
        assert len(warns) == 1


# ═══════════════════════════════════════════════════════════════════════
# 11. Token limit hierarchy
# ═══════════════════════════════════════════════════════════════════════

class TestTokenLimitHierarchy:
    """Verify token_limit_worker <= token_limit_validator <= token_limit_codex."""

    def test_valid_hierarchy_passes(self) -> None:
        m = _valid_manifest(
            token_limit_worker=8000,
            token_limit_validator=16000,
            token_limit_codex=32000,
        )
        report = validate_cross_field_integrity(m)
        assert report.passed

    def test_worker_exceeds_validator_detected(self) -> None:
        m = _valid_manifest(
            token_limit_worker=25000,
            token_limit_validator=20000,
            token_limit_codex=30000,
        )
        report = validate_cross_field_integrity(m)
        assert not report.passed
        err = next(
            e for e in report.errors
            if e.rule_id == "token_limit_worker_exceeds_validator"
        )
        assert "25000" in err.message

    def test_validator_exceeds_codex_detected(self) -> None:
        m = _valid_manifest(
            token_limit_worker=12000,
            token_limit_validator=35000,
            token_limit_codex=30000,
        )
        report = validate_cross_field_integrity(m)
        assert not report.passed
        err = next(
            e for e in report.errors
            if e.rule_id == "token_limit_validator_exceeds_codex"
        )
        assert "35000" in err.message


# ═══════════════════════════════════════════════════════════════════════
# 12. Null and non-dict input
# ═══════════════════════════════════════════════════════════════════════

class TestNullAndNonDictInput:
    """Verify graceful handling of None and non-dict inputs."""

    def test_none_input_returns_failed(self) -> None:
        report = validate_cross_field_integrity(None)
        assert not report.passed
        assert report.error_count >= 1
        assert report.rule_count == 0

    def test_list_input_returns_failed(self) -> None:
        report = validate_cross_field_integrity([1, 2, 3])  # type: ignore
        assert not report.passed
        assert any(
            e.rule_id == "non_dict_input" for e in report.errors
        )

    def test_string_input_returns_failed(self) -> None:
        report = validate_cross_field_integrity("not a dict")  # type: ignore
        assert not report.passed

    def test_int_input_returns_failed(self) -> None:
        report = validate_cross_field_integrity(42)  # type: ignore
        assert not report.passed


# ═══════════════════════════════════════════════════════════════════════
# 13. Fuzzed data — corrupted references and inconsistent states
# ═══════════════════════════════════════════════════════════════════════

class TestFuzzedData:
    """Verify the validator handles deliberately corrupted/fuzzed data."""

    def test_fuzzed_corrupted_speaker_reference(self) -> None:
        """Speaker is a dict instead of a string."""
        m = _valid_manifest(current_speaker={"role": "hack"})  # type: ignore
        report = validate_cross_field_integrity(m)
        # Should not crash; the speaker won't match any role
        assert not report.passed

    def test_fuzzed_context_packets_not_a_list(self) -> None:
        m = _valid_manifest(context_packets="corrupted")  # type: ignore
        report = validate_cross_field_integrity(m)
        # Should not crash — rule should handle non-list gracefully
        assert isinstance(report, CrossFieldReport)

    def test_fuzzed_decisions_not_a_list(self) -> None:
        m = _valid_manifest(decisions=42)  # type: ignore
        report = validate_cross_field_integrity(m)
        assert isinstance(report, CrossFieldReport)

    def test_fuzzed_tool_outputs_not_a_list(self) -> None:
        m = _valid_manifest(tool_outputs=None)  # type: ignore
        report = validate_cross_field_integrity(m)
        assert isinstance(report, CrossFieldReport)

    def test_fuzzed_round_count_negative(self) -> None:
        m = _valid_manifest(round_count=-5)
        report = validate_cross_field_integrity(m)
        # Negative round_count passed to sub-collection checks will
        # result in max_round_seen=0 > -5, triggering round_count_too_low
        assert not report.passed

    def test_fuzzed_validation_score_string(self) -> None:
        m = _valid_manifest(
            validation_verdict="pass",
            validation_score="not-a-number",  # type: ignore
        )
        report = validate_cross_field_integrity(m)
        assert not report.passed
        err = next(
            e for e in report.errors
            if e.rule_id == "verdict_without_valid_score"
        )
        assert err is not None

    def test_fuzzed_max_agents_negative(self) -> None:
        m = _valid_manifest(
            max_agents_per_meeting=-1,
            required_roles=["a"],
            context_packets=[
                {"round": 1, "role_id": "a",
                 "created_at": "2026-06-10T14:30:00Z"},
            ],
        )
        report = validate_cross_field_integrity(m)
        # 1 unique role > -1 max → should trigger participant_limit_exceeded
        assert not report.passed
        err = next(
            e for e in report.errors
            if e.rule_id == "participant_limit_exceeded"
        )
        assert err is not None

    def test_fuzzed_max_rounds_zero(self) -> None:
        """max_rounds=0 means max allowed round is 1 (tie-break)."""
        m = _valid_manifest(
            max_rounds=0,
            context_packets=[
                {"round": 2, "role_id": "art-director",
                 "created_at": "2026-06-10T14:30:00Z"},
            ],
        )
        report = validate_cross_field_integrity(m)
        assert not report.passed
        err = next(
            e for e in report.errors
            if e.rule_id == "packet_round_exceeds_max"
        )
        assert err is not None

    def test_fuzzed_nested_garbage_in_packets(self) -> None:
        """Context packets contain deeply nested garbage that should not crash."""
        m = _valid_manifest(context_packets=[
            {"round": {"nested": "garbage"}, "role_id": 12345},
            42,
            "string-instead-of-dict",
        ])
        report = validate_cross_field_integrity(m)
        assert isinstance(report, CrossFieldReport)
        # Should not crash

    def test_inconsistent_cross_field_state(self) -> None:
        """Multiple simultaneous inconsistencies."""
        m = _valid_manifest(
            state="completed",
            round_count=0,
            validation_verdict="pass",
            validation_score=0.5,
            consensus="",
            current_speaker="ghost-role",
            risk_tags=["legal"],
            codex_required=False,
            validator_required=False,
            token_limit_worker=50000,
            token_limit_validator=10000,
        )
        report = validate_cross_field_integrity(m)
        assert not report.passed
        assert report.error_count >= 4


# ═══════════════════════════════════════════════════════════════════════
# 14. Report properties and error aggregation
# ═══════════════════════════════════════════════════════════════════════

class TestReportProperties:
    """Verify CrossFieldReport properties and methods."""

    def test_error_count_distinguishes_errors_from_warnings(self) -> None:
        m = _valid_manifest(
            state="completed",
            round_count=0,  # error
            consensus="",  # warning only
            validation_score=0.92,
            validation_verdict="pass",
        )
        report = validate_cross_field_integrity(m)
        assert report.error_count >= 1  # completed_without_rounds
        # There may be warnings too

    def test_warning_count(self) -> None:
        m = _valid_manifest(
            state="failed",
            error_log=[],  # terminal_state_without_errors warning
            round_count=2,
            consensus="Done.",
        )
        report = validate_cross_field_integrity(m)
        # Warning only — should pass
        assert report.warning_count >= 1
        assert report.error_count == 0

    def test_errors_by_category_groups(self) -> None:
        m = _valid_manifest(
            current_speaker="ghost-role",  # reference_integrity
            token_limit_worker=50000,  # validation_semantics
            token_limit_validator=10000,
        )
        report = validate_cross_field_integrity(m)
        grouped = report.errors_by_category()
        assert "reference_integrity" in grouped
        assert "validation_semantics" in grouped

    def test_multiple_errors_in_same_category(self) -> None:
        m = _valid_manifest(
            speaker_queue=["ghost-1", "ghost-2"],
            context_packets=[
                {"round": 1, "role_id": "alien-1",
                 "created_at": "2026-06-10T14:30:00Z"},
                {"round": 1, "role_id": "alien-2",
                 "created_at": "2026-06-10T14:30:00Z"},
            ],
        )
        report = validate_cross_field_integrity(m)
        grouped = report.errors_by_category()
        ref_errs = grouped.get("reference_integrity", ())
        assert len(ref_errs) >= 3  # 2 queue + 2 packets = 4


# ═══════════════════════════════════════════════════════════════════════
# 15. Edge cases and boundary conditions
# ═══════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    """Verify edge cases and boundary conditions."""

    def test_empty_required_and_optional_roles(self) -> None:
        """When role lists are empty, no speaker/packet roles should be valid."""
        m = _valid_manifest(
            required_roles=[],
            optional_roles=[],
            current_speaker="some-role",
            context_packets=[
                {"round": 1, "role_id": "some-role",
                 "created_at": "2026-06-10T14:30:00Z"},
            ],
        )
        report = validate_cross_field_integrity(m)
        assert not report.passed

    def test_tuples_instead_of_lists_accepted(self) -> None:
        """Tuples should be accepted (MeetingManifest stores tuples)."""
        m = _valid_manifest(
            required_roles=("coordinator", "art-director"),
            optional_roles=("concept-artist",),
            round_count=2,
            current_speaker="art-director",
            context_packets=[
                {"round": 1, "role_id": "art-director",
                 "created_at": "2026-06-10T14:30:00Z"},
                {"round": 2, "role_id": "coordinator",
                 "created_at": "2026-06-10T14:35:00Z"},
            ],
            decisions=[],
        )
        report = validate_cross_field_integrity(m)
        assert report.passed

    def test_score_exactly_085_passes(self) -> None:
        m = _valid_manifest(
            state="completed",
            validation_verdict="pass",
            validation_score=0.85,
            round_count=2,
            consensus="Done.",
        )
        report = validate_cross_field_integrity(m)
        assert report.error_count == 0

    def test_score_084999_fails(self) -> None:
        m = _valid_manifest(
            state="completed",
            validation_verdict="pass",
            validation_score=0.84999,
            round_count=2,
            consensus="Done.",
        )
        report = validate_cross_field_integrity(m)
        assert not report.passed
        err = next(
            e for e in report.errors
            if e.rule_id == "completed_pass_score_too_low"
        )
        assert err is not None

    def test_verdict_fail_does_not_check_score(self) -> None:
        """Only 'pass' verdict triggers the 0.85 threshold."""
        m = _valid_manifest(
            state="completed",
            validation_verdict="fail",
            validation_score=0.3,
            round_count=2,
            consensus="Failed.",
        )
        report = validate_cross_field_integrity(m)
        # Should not have completed_pass_score_too_low
        errs = [
            e for e in report.errors
            if e.rule_id == "completed_pass_score_too_low"
        ]
        assert len(errs) == 0

    def test_all_rules_evaluated(self) -> None:
        """Verify all registered rules are evaluated."""
        report = validate_cross_field_integrity(_valid_manifest())
        assert report.rule_count == len(_RULES)
        assert report.rule_count >= 10
