from __future__ import annotations

from pathlib import Path

from src.meeting_control_plane import (
    bootstrap_persona_registry,
    evaluate_execution_controls,
    evaluate_meeting_round_controls,
    route_thread_followup,
)
from src.meeting_scheduler import RunningMeeting
from src.openclaw_approval import OpenClawAction
from src.quorum_policy import RoleResult


def test_evaluate_meeting_round_controls_combines_preemption_and_quorum():
    result = evaluate_meeting_round_controls(
        running=(
            RunningMeeting("m-low-1", "P2", "round_1"),
            RunningMeeting("m-low-2", "P3", "round_1"),
        ),
        incoming_meeting_id="m-p0",
        incoming_priority="P0",
        roles=(
            RoleResult("lead", True, "completed"),
            RoleResult("engineer", True, "failed", attempts=0, max_attempts=2),
        ),
        required_quorum=2,
        max_concurrent=2,
    )

    assert result.preemption.started.meeting_id == "m-p0"
    assert result.preemption.paused is not None
    assert result.preemption.paused.meeting_id == "m-low-2"
    assert result.quorum.decision == "retry_required_role"
    assert result.quorum.next_role_id == "engineer"


def test_evaluate_execution_controls_blocks_high_risk_and_rejects_parameter_mutation():
    result = evaluate_execution_controls(
        action=OpenClawAction(
            execution_id="exec-1",
            action_type="file_write",
            risk_level="high",
            target="prod-config",
        ),
        intervention_type="modify_parameters",
        intervention_reason="change args while running",
    )

    assert result.approval.requires_approval
    assert not result.approval.allowed_to_execute
    assert result.intervention is not None
    assert result.intervention.state == "rejected"


def test_bootstrap_persona_registry_loads_specs_and_team_leaders(tmp_path: Path):
    leader = tmp_path / "creative_lead"
    worker = tmp_path / "engineer"
    leader.mkdir()
    worker.mkdir()
    (leader / "agent.yaml").write_text(
        "role_id: creative_lead\nteam: creative\nkind: team_leader\n",
        encoding="utf-8",
    )
    (leader / "persona.md").write_text("# Creative Lead", encoding="utf-8")
    (worker / "agent.yaml").write_text(
        "role_id: engineer\nteam: technical\nkind: worker\n",
        encoding="utf-8",
    )
    (worker / "persona.md").write_text("# Engineer", encoding="utf-8")

    result = bootstrap_persona_registry((leader, worker), git_version="v1")

    assert [spec.role_id for spec in result.persona_specs] == ["creative_lead", "engineer"]
    assert result.registry.bot_role_ids == ("creative_lead",)
    assert result.registry.worker_only_role_ids == ("engineer",)


def test_route_thread_followup_extends_existing_meeting():
    result = route_thread_followup(
        thread_id="thread-1",
        text="추가로 예산도 검토해줘",
        thread_to_meeting={"thread-1": "meeting-1"},
    )

    assert result.action == "extend_existing_meeting"
    assert result.meeting_id == "meeting-1"
