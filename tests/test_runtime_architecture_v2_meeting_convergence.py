from src.runtime_architecture_v2.meeting_convergence import (
    ConvergencePolicy,
    decide_convergence,
    disagreement_fingerprint,
)
from src.runtime_architecture_v2.schemas import MeetingOutcome

PARTICIPANTS = (
    "ceo_coordinator",
    "content_lead",
    "art_lead",
    "tech_lead",
    "marketing_lead",
    "validation_audit",
)


def _outcome(
    status: str,
    *,
    disagreements: tuple[str, ...] = (),
    unresolved_roles: tuple[str, ...] = (),
    generation_status: str = "live",
) -> MeetingOutcome:
    return MeetingOutcome(
        meeting_run_id="mr_convergence",
        status=status,
        summary="회의 결과",
        disagreements=disagreements,
        unresolved_roles=unresolved_roles,
        generation_status=generation_status,
    )


def test_agreement_and_failed_evaluation_stop_without_more_rounds() -> None:
    agreed = decide_convergence(
        (_outcome("agreed"),),
        participants=PARTICIPANTS,
        completed_rounds=2,
    )
    failed = decide_convergence(
        (
            _outcome(
                "needs_user_decision",
                generation_status="failed",
            ),
        ),
        participants=PARTICIPANTS,
        completed_rounds=2,
    )

    assert (agreed.action, agreed.reason, agreed.next_roles) == (
        "stop",
        "agreed",
        (),
    )
    assert (failed.action, failed.reason, failed.next_roles) == (
        "stop",
        "outcome_failed",
        (),
    )


def test_partial_agreement_continues_only_unresolved_roles_plus_validation() -> None:
    decision = decide_convergence(
        (
            _outcome(
                "partial_agreement",
                disagreements=("출시 시점",),
                unresolved_roles=("marketing_lead", "content_lead"),
            ),
        ),
        participants=PARTICIPANTS,
        completed_rounds=2,
    )

    assert decision.action == "continue"
    assert decision.next_roles == (
        "content_lead",
        "marketing_lead",
        "validation_audit",
    )


def test_missing_role_ownership_falls_back_to_all_participants() -> None:
    decision = decide_convergence(
        (
            _outcome(
                "blocked",
                disagreements=("책임 주체가 정해지지 않음",),
            ),
        ),
        participants=PARTICIPANTS,
        completed_rounds=2,
    )

    assert decision.action == "continue"
    assert decision.next_roles == PARTICIPANTS
    assert decision.reason == "unresolved_roles_missing"


def test_non_agreement_without_a_concrete_disagreement_stops() -> None:
    decision = decide_convergence(
        (_outcome("blocked", unresolved_roles=("content_lead",)),),
        participants=PARTICIPANTS,
        completed_rounds=2,
    )

    assert decision.action == "stop"
    assert decision.reason == "unstructured_disagreement"
    assert decision.next_roles == ()


def test_same_disagreement_twice_requests_ceo_arbitration() -> None:
    outcomes = (
        _outcome(
            "partial_agreement",
            disagreements=("출시 일정 충돌",),
            unresolved_roles=("content_lead", "marketing_lead"),
        ),
        _outcome(
            "blocked",
            disagreements=("  출시   일정 충돌 ",),
            unresolved_roles=("content_lead", "marketing_lead"),
        ),
    )

    decision = decide_convergence(
        outcomes,
        participants=PARTICIPANTS,
        completed_rounds=3,
    )

    assert disagreement_fingerprint(outcomes[0]) == disagreement_fingerprint(
        outcomes[1]
    )
    assert decision.action == "arbitrate"
    assert decision.reason == "repeated_disagreement"
    assert decision.next_roles == ("ceo_coordinator",)


def test_round_six_stops_for_user_decision_instead_of_forcing_agreement() -> None:
    decision = decide_convergence(
        (
            _outcome(
                "partial_agreement",
                disagreements=("예산 승인",),
                unresolved_roles=("ceo_coordinator", "marketing_lead"),
            ),
        ),
        participants=PARTICIPANTS,
        completed_rounds=6,
        policy=ConvergencePolicy(max_rounds=6),
    )

    assert decision.action == "stop"
    assert decision.reason == "max_rounds_reached"
    assert decision.next_roles == ()
