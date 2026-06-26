from __future__ import annotations

from src.runtime_architecture_v2.policies import QuotaPolicy, QuotaSnapshot
from src.runtime_architecture_v2.schemas import (
    ValidationVerdict,
    ValidationVerdictValue,
    WorkerTaskRunner,
)
from src.runtime_architecture_v2.validation import (
    CorrectionActionKind,
    ValidationPolicy,
    ValidatorExecutionPlanner,
    ValidatorRolePolicy,
    build_degraded_verdict,
)


def _verdict(
    validation_id: str,
    verdict: ValidationVerdictValue | str,
    *,
    confidence: float = 0.9,
    required_actions: tuple[str, ...] = (),
    degraded_reason: str = "",
) -> ValidationVerdict:
    return ValidationVerdict(
        validation_id=validation_id,
        meeting_run_id="mr_001",
        validator_role="glm_validator",
        validator_model="glm-5.1",
        verdict=verdict,
        confidence=confidence,
        findings=("checked",),
        required_actions=required_actions,
        degraded_reason=degraded_reason,
    )


def test_validation_policy_passes_when_all_verdicts_are_non_blocking():
    decision = ValidationPolicy().decide(
        meeting_run_id="mr_001",
        verdicts=(
            _verdict("v_glm", ValidationVerdictValue.PASS),
            _verdict(
                "v_codex",
                ValidationVerdictValue.CONDITIONAL_PASS,
                required_actions=("mention minor caveat in final report",),
            ),
        ),
    )

    assert decision.kind is CorrectionActionKind.CONTINUE
    assert decision.next_state == "reporting"
    assert decision.blocking_validation_ids == ()
    assert decision.required_actions == ("mention minor caveat in final report",)


def test_validation_policy_revise_creates_follow_up_worker_action():
    decision = ValidationPolicy().decide(
        meeting_run_id="mr_001",
        verdicts=(
            _verdict(
                "v_glm",
                ValidationVerdictValue.REVISE,
                required_actions=("rerun tech worker with missing test evidence",),
            ),
        ),
    )

    assert decision.kind is CorrectionActionKind.REVISE
    assert decision.next_state == "active"
    assert decision.follow_up_worker_required is True
    assert decision.blocking_validation_ids == ("v_glm",)
    assert decision.required_actions == (
        "rerun tech worker with missing test evidence",
    )


def test_validation_policy_reject_stops_and_reports_failure():
    decision = ValidationPolicy().decide(
        meeting_run_id="mr_001",
        verdicts=(_verdict("v_codex", ValidationVerdictValue.REJECT),),
    )

    assert decision.kind is CorrectionActionKind.STOP
    assert decision.next_state == "failed"
    assert decision.requires_user is False
    assert decision.blocking_validation_ids == ("v_codex",)


def test_validation_policy_legacy_fail_is_reject_equivalent():
    decision = ValidationPolicy().decide(
        meeting_run_id="mr_001",
        verdicts=(_verdict("v_legacy", ValidationVerdictValue.FAIL),),
    )

    assert decision.kind is CorrectionActionKind.STOP
    assert decision.next_state == "failed"
    assert decision.blocking_validation_ids == ("v_legacy",)


def test_validation_policy_preserves_blocking_precedence():
    policy = ValidationPolicy()

    reject_over_revise = policy.decide(
        meeting_run_id="mr_001",
        verdicts=(
            _verdict("v_revise", ValidationVerdictValue.REVISE),
            _verdict("v_reject", ValidationVerdictValue.REJECT),
        ),
    )
    escalate_over_revise = policy.decide(
        meeting_run_id="mr_001",
        verdicts=(
            _verdict("v_revise", ValidationVerdictValue.REVISE),
            _verdict("v_escalate", ValidationVerdictValue.ESCALATE),
        ),
    )

    assert reject_over_revise.kind is CorrectionActionKind.STOP
    assert reject_over_revise.blocking_validation_ids == ("v_reject",)
    assert escalate_over_revise.kind is CorrectionActionKind.ASK_USER
    assert escalate_over_revise.blocking_validation_ids == ("v_escalate",)


def test_validation_policy_escalate_asks_user_without_silent_success():
    decision = ValidationPolicy().decide(
        meeting_run_id="mr_001",
        verdicts=(_verdict("v_glm", ValidationVerdictValue.ESCALATE),),
    )

    assert decision.kind is CorrectionActionKind.ASK_USER
    assert decision.next_state == "paused"
    assert decision.requires_user is True
    assert decision.blocking_validation_ids == ("v_glm",)


def test_unavailable_validator_produces_explicit_degraded_verdict():
    verdict = build_degraded_verdict(
        validation_id="v_glm_unavailable",
        meeting_run_id="mr_001",
        validator_role="glm_validator",
        validator_model="glm-5.1",
        reason="opencode-go quota unavailable",
    )

    assert verdict.verdict is ValidationVerdictValue.DEGRADED
    assert verdict.confidence == 0.0
    assert verdict.degraded_reason == "opencode-go quota unavailable"

    decision = ValidationPolicy().decide(meeting_run_id="mr_001", verdicts=(verdict,))
    assert decision.kind is CorrectionActionKind.ASK_USER
    assert decision.requires_user is True
    assert decision.blocking_validation_ids == ("v_glm_unavailable",)


def test_validation_policy_fails_closed_when_no_verdicts_exist():
    decision = ValidationPolicy().decide(meeting_run_id="mr_001", verdicts=())

    assert decision.kind is CorrectionActionKind.ASK_USER
    assert decision.next_state == "paused"
    assert decision.requires_user is True
    assert decision.rationale == "missing validation evidence"


def test_validation_policy_rejects_mixed_meeting_run_verdicts():
    wrong_run_verdict = ValidationVerdict(
        validation_id="v_wrong_run",
        meeting_run_id="mr_other",
        validator_role="glm_validator",
        validator_model="glm-5.1",
        verdict=ValidationVerdictValue.PASS,
        confidence=1.0,
    )

    decision = ValidationPolicy().decide(
        meeting_run_id="mr_001",
        verdicts=(_verdict("v_glm", ValidationVerdictValue.PASS), wrong_run_verdict),
    )

    assert decision.kind is CorrectionActionKind.ASK_USER
    assert decision.next_state == "paused"
    assert decision.requires_user is True
    assert decision.blocking_validation_ids == ("v_wrong_run",)
    assert decision.rationale == "validation verdict meeting_run_id mismatch"


def test_glm_and_codex_validator_roles_use_opencode_go_first(tmp_path):
    glm_task = ValidatorRolePolicy.glm_validator().build_worker_task(
        meeting_run_id="mr_001",
        validation_id="v_glm",
        packet_path=tmp_path / "packets" / "v_glm.json",
        output_path=tmp_path / "validation" / "v_glm.json",
    )
    codex_task = ValidatorRolePolicy.codex_auditor().build_worker_task(
        meeting_run_id="mr_001",
        validation_id="v_codex",
        packet_path=tmp_path / "packets" / "v_codex.json",
        output_path=tmp_path / "validation" / "v_codex.json",
    )

    assert glm_task.runner is WorkerTaskRunner.OPENCODE_GO
    assert glm_task.role == "glm_validator"
    assert glm_task.model_policy == {
        "preferred": "glm-5.1",
        "execution_role": "validator",
        "model_family": "glm",
        "fallback_runner": "none",
    }
    assert codex_task.runner is WorkerTaskRunner.OPENCODE_GO
    assert codex_task.role == "codex_auditor"
    assert codex_task.model_policy["preferred"] == "codex"
    assert (
        codex_task.model_policy["fallback_runner"]
        == "codex_cli_only_if_opencode_go_unavailable"
    )
    assert "codex_cli" not in {codex_task.runner.value, codex_task.role}


def test_validator_execution_planner_builds_opencode_first_tasks_when_quota_allows(
    tmp_path,
):
    plan = ValidatorExecutionPlanner(root=tmp_path).plan(
        meeting_run_id="mr_001",
        validators=("glm_validator", "codex_auditor"),
        quota_policy=QuotaPolicy(),
        active_provider="opencode-go",
    )

    assert plan.status == "ready"
    assert plan.quota_decision.allowed is True
    assert plan.degraded_verdicts == ()
    assert [task.role for task in plan.worker_tasks] == [
        "glm_validator",
        "codex_auditor",
    ]
    assert all(
        task.runner is WorkerTaskRunner.OPENCODE_GO for task in plan.worker_tasks
    )
    assert plan.worker_tasks[0].model_policy["preferred"] == "glm-5.1"
    assert plan.worker_tasks[1].model_policy["fallback_runner"] == (
        "codex_cli_only_if_opencode_go_unavailable"
    )


def test_validator_execution_planner_rejects_unsafe_meeting_run_ids(tmp_path):
    for meeting_run_id in (".", "..", ".hidden", "bad/path"):
        plan = ValidatorExecutionPlanner(root=tmp_path).plan(
            meeting_run_id=meeting_run_id,
            validators=("glm_validator",),
            quota_policy=QuotaPolicy(),
            active_provider="opencode-go",
        )
        assert plan.status == "invalid_meeting_run_id"
        assert plan.quota_decision.allowed is False
        assert plan.worker_tasks == ()
        assert plan.degraded_verdicts == ()


def test_validator_execution_planner_degrades_without_dispatch_when_quota_blocks(
    tmp_path,
):
    plan = ValidatorExecutionPlanner(root=tmp_path).plan(
        meeting_run_id="mr_001",
        validators=("glm_validator", "codex_auditor"),
        quota_policy=QuotaPolicy(
            snapshot=QuotaSnapshot(
                provider="opencode-go",
                monthly_percent=100,
                weekly_percent=0,
                hourly_percent=0,
            )
        ),
        active_provider="opencode-go",
    )

    assert plan.status == "quota_blocked"
    assert plan.quota_decision.allowed is False
    assert plan.worker_tasks == ()
    assert [verdict.validator_role for verdict in plan.degraded_verdicts] == [
        "glm_validator",
        "codex_auditor",
    ]
    assert all(
        verdict.verdict is ValidationVerdictValue.DEGRADED
        for verdict in plan.degraded_verdicts
    )
    assert all(
        "quota" in verdict.degraded_reason for verdict in plan.degraded_verdicts
    )

