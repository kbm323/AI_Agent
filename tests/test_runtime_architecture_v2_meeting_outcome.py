from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.runtime_architecture_v2.meeting_outcome import evaluate_meeting_outcome
from src.runtime_architecture_v2.multi_bot import (
    BotMessage,
    MeetingRound,
    MultiBotSession,
)
from src.runtime_architecture_v2.workers import OpenCodeGoRunResult

VISIBLE_ROLES = (
    "ceo_coordinator",
    "content_lead",
    "art_lead",
    "tech_lead",
    "marketing_lead",
    "validation_audit",
)


def _session(*, live_roles: tuple[str, ...] = VISIBLE_ROLES) -> MultiBotSession:
    rounds = []
    for round_number, phase in ((1, "opinions"), (2, "rebuttals")):
        messages = tuple(
            BotMessage(
                bot_role=role,
                meeting_run_id="mr_outcome",
                round=round_number,
                msg_type="opinion" if round_number == 1 else "rebuttal",
                content=f"{role}의 {round_number}라운드 근거",
                generation_status="live" if role in live_roles else "replacement",
                provider="opencode-go",
                model="test-model",
                error_code="" if role in live_roles else "provider_error",
            )
            for role in VISIBLE_ROLES
        )
        rounds.append(
            MeetingRound(
                round_number=round_number,
                phase=phase,  # type: ignore[arg-type]
                messages=messages,
            )
        )
    return MultiBotSession(
        meeting_run_id="mr_outcome",
        participants=VISIBLE_ROLES,
        rounds=tuple(rounds),
        consensus_reached=False,
        escalation_required=True,
    )


def _runner(payload: object):
    def command_runner(command: list[str], timeout_seconds: int, workdir: str | None):
        return OpenCodeGoRunResult(
            exit_code=0,
            stdout=json.dumps(payload, ensure_ascii=False),
            stderr="",
            timeout_occurred=False,
            duration_seconds=0.01,
        )

    return command_runner


def _payload(status: str = "agreed") -> dict[str, object]:
    return {
        "status": status,
        "summary": "콘텐츠 방향과 검증 조건에 합의했습니다.",
        "agreements": ["콘텐츠 방향"],
        "disagreements": [] if status == "agreed" else ["출시 일정"],
        "action_items": ["검증 조건을 문서화한다"],
        "evidence_refs": [
            "round:1:content_lead",
            "round:2:validation_audit",
        ],
        "validator_notes": ["근거 확인 완료"],
    }


@pytest.mark.parametrize(
    "status",
    ("agreed", "partial_agreement", "blocked", "needs_user_decision"),
)
def test_evaluate_meeting_outcome_accepts_supported_statuses(
    tmp_path: Path,
    status: str,
):
    outcome = evaluate_meeting_outcome(
        _session(),
        command_runner=_runner(_payload(status)),
        workdir=tmp_path,
    )

    assert outcome.status == status
    assert outcome.generation_status == "live"
    assert outcome.evidence_refs
    assert outcome.model


def test_agreement_requires_all_six_live_roles(tmp_path: Path):
    outcome = evaluate_meeting_outcome(
        _session(live_roles=VISIBLE_ROLES[:-1]),
        command_runner=_runner(_payload("agreed")),
        workdir=tmp_path,
    )

    assert outcome.status == "needs_user_decision"
    assert outcome.error_code == "insufficient_live_evidence"


def test_partial_agreement_requires_four_roles_and_live_validation(tmp_path: Path):
    without_validation = VISIBLE_ROLES[:4]
    outcome = evaluate_meeting_outcome(
        _session(live_roles=without_validation),
        command_runner=_runner(_payload("partial_agreement")),
        workdir=tmp_path,
    )

    assert outcome.status == "needs_user_decision"
    assert outcome.error_code == "insufficient_live_evidence"


@pytest.mark.parametrize(
    ("payload", "expected_error"),
    (
        ("not-json", "invalid_outcome_json"),
        ({"status": "unknown"}, "invalid_outcome_status"),
        (
            {
                "status": "agreed",
                "summary": "근거 없는 합의",
                "agreements": ["합의"],
                "evidence_refs": [],
            },
            "missing_outcome_evidence",
        ),
    ),
)
def test_invalid_outcome_fails_closed(
    tmp_path: Path,
    payload: object,
    expected_error: str,
):
    outcome = evaluate_meeting_outcome(
        _session(),
        command_runner=_runner(payload),
        workdir=tmp_path,
    )

    assert outcome.status == "needs_user_decision"
    assert outcome.generation_status == "failed"
    assert outcome.error_code == expected_error
    assert outcome.agreements == ()
    assert outcome.action_items == ()


def test_provider_failure_fails_closed_without_raw_error(tmp_path: Path):
    def failing_runner(*_args, **_kwargs):
        raise RuntimeError("secret provider detail")

    outcome = evaluate_meeting_outcome(
        _session(),
        command_runner=failing_runner,
        workdir=tmp_path,
    )

    assert outcome.status == "needs_user_decision"
    assert outcome.error_code == "outcome_provider_error"
    assert "secret" not in json.dumps(outcome.to_dict())
