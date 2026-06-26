"""Phase 30 team synthesis tests.

Verify that completed worker tasks across 29 internal roles are correctly
grouped by projection profile for the 7 Discord-facing Hermes profiles.
"""

from __future__ import annotations

from pathlib import Path

from src.runtime_architecture_v2.model_policy import load_role_model_policies
from src.runtime_architecture_v2.schemas import WorkerTask, WorkerTaskRunner
from src.runtime_architecture_v2.team_synthesis import (
    TeamSynthesis,
    _HERMES_PROJECTION_PROFILES,
    synthesize_worker_results,
)

ROOT = Path(__file__).resolve().parent.parent
RULES_PATH = ROOT / "config" / "routing_rules.yaml"


def _make_task(
    role_id: str,
    meeting_run_id: str = "mr_synth_test",
    index: int = 1,
    output_summary: str = "",
) -> WorkerTask:
    return WorkerTask(
        worker_task_id=f"wt_{meeting_run_id}_{index}_{role_id}",
        meeting_run_id=meeting_run_id,
        role=role_id,
        runner=WorkerTaskRunner.OPENCODE_GO,
        model_policy={"preferred": "glm-5.1"},
        packet_path=str(ROOT / "runtime" / "meeting_runs" / meeting_run_id / "packets" / f"wt_{index}.json"),
        output_path=str(ROOT / "runtime" / "meeting_runs" / meeting_run_id / "worker_outputs" / f"wt_{index}.json"),
    )


def test_synthesize_groups_all_29_roles_by_profile():
    policies = load_role_model_policies(RULES_PATH)
    tasks = tuple(
        _make_task(role_id, index=i)
        for i, role_id in enumerate(policies, start=1)
    )

    synthesis = synthesize_worker_results("mr_all_29", tasks)

    assert synthesis.total_specialists <= 29
    assert 1 <= synthesis.profile_count <= 7
    assert set(synthesis.groups.keys()).issubset(set(_HERMES_PROJECTION_PROFILES))

    # Verify the content team
    content_roles = {
        r.role_id for r in synthesis.groups.get("aicompanycontent", ())
    }
    assert "content-director" in content_roles
    assert "scriptwriter" in content_roles

    # Verify quality/validation
    quality_roles = {
        r.role_id for r in synthesis.groups.get("aicompanyquality", ())
    }
    assert "validator" in quality_roles


def test_empty_tasks_returns_empty_groups():
    synthesis = synthesize_worker_results("mr_empty", ())
    assert synthesis.total_specialists == 0
    assert synthesis.profile_count == 0
    assert synthesis.groups == {}


def test_unknown_role_added_to_uncovered():
    fake_task = _make_task("not-a-known-role")
    synthesis = synthesize_worker_results("mr_fake", (fake_task,))
    assert "not-a-known-role" in synthesis.uncovered_roles
    assert synthesis.total_specialists == 0


def test_projectable_summary_includes_role_counts():
    tasks = tuple(
        _make_task(role_id, index=i)
        for i, role_id in enumerate(
            ("content-director", "scriptwriter", "validator", "marketing-lead"),
            start=1,
        )
    )
    synthesis = synthesize_worker_results("mr_partial", tasks)
    summary = synthesis.as_projectable_summary()

    assert any("콘텐츠" in v for v in summary.values())
    assert any("검증" in v for v in summary.values())
    assert any("마케팅" in v for v in summary.values())
    assert synthesis.profile_count >= 2


def test_team_synthesis_records_meeting_run_id():
    tasks = (_make_task("content-director", meeting_run_id="mr_42"),)
    synthesis = synthesize_worker_results("mr_42", tasks)
    assert synthesis.meeting_run_id == "mr_42"
    assert synthesis.total_specialists == 1


def test_team_synthesis_fail_closed_on_mixed_ids():
    """Uncovered roles must not silently pass — they must be tracked."""
    tasks = (_make_task("bogus-role-xyz"),)
    synthesis = synthesize_worker_results("mr_test", tasks)
    assert "bogus-role-xyz" in synthesis.uncovered_roles
    assert synthesis.total_specialists == 0


def test_synthesize_with_legacy_alias_resolves_correct_team():
    """Legacy pilot IDs such as content_lead should normalize to canonical
    role IDs before looking up policy metadata (team, role_type)."""
    tasks = (_make_task("content_lead"), _make_task("quality_lead"))
    synthesis = synthesize_worker_results("mr_legacy", tasks)

    assert synthesis.total_specialists == 2
    content = next(
        r for results in synthesis.groups.values() for r in results
        if r.role_id == "content-director"
    )
    assert content.team == "content-production"
    assert content.role_type == "leader"

    validator = next(
        r for results in synthesis.groups.values() for r in results
        if r.role_id == "validator"
    )
    assert validator.team == "validation"
    assert validator.role_type == "validator"
