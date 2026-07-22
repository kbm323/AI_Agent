"""Evidence-based outcome evaluation for Runtime Architecture v2 meetings."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from .model_policy import worker_model_policy_for_role
from .multi_bot import MultiBotSession
from .schemas import (
    MeetingOutcome,
    MeetingOutcomeStatus,
    WorkerTask,
    WorkerTaskRunner,
    WorkerTaskState,
)
from .workers import (
    OpenCodeGoCommandRunner,
    OpenCodeGoWorkerRunner,
)

_VISIBLE_ROLES = frozenset(
    {
        "ceo_coordinator",
        "content_lead",
        "art_lead",
        "tech_lead",
        "marketing_lead",
        "validation_audit",
    }
)


@dataclass(frozen=True)
class _OutcomeEvaluationError(Exception):
    code: str


def evaluate_meeting_outcome(
    session: MultiBotSession,
    *,
    command_runner: OpenCodeGoCommandRunner | None,
    workdir: str | Path,
    evaluator_role: str = "validation_audit",
    resolution_kind: str = "validation",
    previous_outcome: MeetingOutcome | None = None,
) -> MeetingOutcome:
    """Evaluate a persisted transcript and fail closed on unproven agreement."""

    if resolution_kind not in {"validation", "arbitration"}:
        raise ValueError(f"invalid outcome resolution kind: {resolution_kind}")
    policy = worker_model_policy_for_role(evaluator_role)
    model = str(policy.get("preferred") or policy.get("primary_model") or "glm-5.1")
    prompt = _build_outcome_prompt(
        session,
        evaluator_role=evaluator_role,
        resolution_kind=resolution_kind,
        previous_outcome=previous_outcome,
    )
    try:
        raw = _run_outcome_provider(
            session,
            prompt=prompt,
            model=model,
            policy=policy,
            command_runner=command_runner,
            workdir=Path(workdir),
        )
        payload = _decode_outcome_payload(raw)
        outcome = _outcome_from_payload(
            session,
            payload,
            model=model,
            evaluator_role=evaluator_role,
            resolution_kind=resolution_kind,
        )
        outcome = _apply_live_evidence_gate(session, outcome)
        if (
            resolution_kind == "arbitration"
            and outcome.status != MeetingOutcomeStatus.AGREED
        ):
            return replace(
                outcome,
                status=MeetingOutcomeStatus.NEEDS_USER_DECISION,
                error_code="arbitration_unresolved",
            )
        return outcome
    except _OutcomeEvaluationError as exc:
        return _failed_outcome(
            session.meeting_run_id,
            model=model,
            error_code=exc.code,
            evaluator_role=evaluator_role,
            resolution_kind=resolution_kind,
        )
    except Exception:
        return _failed_outcome(
            session.meeting_run_id,
            model=model,
            error_code="outcome_provider_error",
            evaluator_role=evaluator_role,
            resolution_kind=resolution_kind,
        )


def _run_outcome_provider(
    session: MultiBotSession,
    *,
    prompt: str,
    model: str,
    policy: Mapping[str, object],
    command_runner: OpenCodeGoCommandRunner | None,
    workdir: Path,
) -> str:
    if command_runner is not None:
        try:
            result = command_runner(
                ["opencode-go", "--model", model, "--prompt", prompt],
                timeout_seconds=300,
                workdir=str(workdir),
            )
        except Exception as exc:
            raise _OutcomeEvaluationError("outcome_provider_error") from exc
        if bool(getattr(result, "timeout_occurred", False)):
            raise _OutcomeEvaluationError("outcome_provider_timeout")
        if int(getattr(result, "exit_code", 0)) != 0:
            raise _OutcomeEvaluationError("outcome_provider_failed")
        stdout = str(getattr(result, "stdout", "") or "").strip()
        if not stdout:
            raise _OutcomeEvaluationError("empty_outcome_output")
        return stdout

    run_dir = workdir / "runtime" / "meeting_runs" / session.meeting_run_id
    task = WorkerTask(
        worker_task_id=f"outcome_{session.meeting_run_id}",
        meeting_run_id=session.meeting_run_id,
        role="validation_audit",
        runner=WorkerTaskRunner.OPENCODE_GO,
        packet_path=str(run_dir / "packets" / "meeting_outcome.json"),
        output_path=str(run_dir / "worker_outputs" / "meeting_outcome.json"),
        model_policy=dict(policy),
        hermes_refs={"prompt": prompt},
    )
    runner = OpenCodeGoWorkerRunner(timeout_seconds=300, workdir=str(workdir))
    completed = runner.collect(runner.dispatch(task))
    if completed.state != WorkerTaskState.SUCCEEDED:
        raise _OutcomeEvaluationError("outcome_provider_failed")
    try:
        output = json.loads(Path(completed.output_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        raise _OutcomeEvaluationError("invalid_outcome_json") from exc
    content = output.get("content") if isinstance(output, dict) else None
    if not isinstance(content, str) or not content.strip():
        raise _OutcomeEvaluationError("empty_outcome_output")
    return content


def _build_outcome_prompt(
    session: MultiBotSession,
    *,
    evaluator_role: str,
    resolution_kind: str,
    previous_outcome: MeetingOutcome | None,
) -> str:
    transcript_lines = []
    for round_data in session.rounds:
        for message in round_data.messages:
            evidence_ref = f"round:{round_data.round_number}:{message.bot_role}"
            transcript_lines.append(
                f"[{evidence_ref}] status={message.generation_status} {message.content}"
            )
    transcript = "\n".join(transcript_lines)
    role_instruction = (
        "당신은 반복된 의견 충돌을 판정하는 대표(CEO) 중재자입니다."
        if resolution_kind == "arbitration"
        else "당신은 회의 결과를 검증하는 품질관리 책임자입니다."
    )
    previous = ""
    if previous_outcome is not None:
        previous = (
            "\n직전 미합의 항목: " + "; ".join(previous_outcome.disagreements) + "\n"
        )
    return (
        f"{role_instruction}\n"
        f"평가 역할: {evaluator_role}\n"
        "아래 회의록만 근거로 결과를 JSON 객체 하나로 반환하세요.\n"
        "status는 agreed, partial_agreement, blocked, needs_user_decision 중 하나입니다.\n"
        "필수 키: status, summary, agreements, disagreements, unresolved_roles, action_items, "
        "evidence_refs, validator_notes.\n"
        "unresolved_roles에는 남은 이견을 직접 해결해야 하는 회의 참여자 역할만 넣으세요.\n"
        "evidence_refs에는 제공된 round:<번호>:<역할> 식별자만 사용하세요.\n"
        "근거가 부족하면 needs_user_decision을 선택하세요.\n\n"
        f"{previous}"
        f"{transcript}"
    )


def _decode_outcome_payload(raw: str) -> dict[str, Any]:
    candidate = raw.strip()
    if candidate.startswith("```"):
        lines = candidate.splitlines()
        if len(lines) >= 3 and lines[-1].strip() == "```":
            candidate = "\n".join(lines[1:-1]).strip()
    try:
        value: object = json.loads(candidate)
        if isinstance(value, str):
            value = json.loads(value)
        if isinstance(value, dict) and "status" not in value and "content" in value:
            content = value.get("content")
            if isinstance(content, str):
                value = json.loads(content)
    except (json.JSONDecodeError, TypeError) as exc:
        raise _OutcomeEvaluationError("invalid_outcome_json") from exc
    if not isinstance(value, dict):
        raise _OutcomeEvaluationError("invalid_outcome_json")
    return dict(value)


def _outcome_from_payload(
    session: MultiBotSession,
    payload: Mapping[str, object],
    *,
    model: str,
    evaluator_role: str,
    resolution_kind: str,
) -> MeetingOutcome:
    status_text = str(payload.get("status", ""))
    try:
        status = MeetingOutcomeStatus(status_text)
    except ValueError as exc:
        raise _OutcomeEvaluationError("invalid_outcome_status") from exc

    evidence_refs = _string_tuple(payload.get("evidence_refs"))
    valid_refs = {
        f"round:{round_data.round_number}:{message.bot_role}"
        for round_data in session.rounds
        for message in round_data.messages
    }
    if status == MeetingOutcomeStatus.AGREED and not evidence_refs:
        raise _OutcomeEvaluationError("missing_outcome_evidence")
    if any(reference not in valid_refs for reference in evidence_refs):
        raise _OutcomeEvaluationError("invalid_outcome_evidence")
    unresolved_roles = _string_tuple(payload.get("unresolved_roles"))
    participant_set = set(session.participants)
    if any(role not in participant_set for role in unresolved_roles):
        raise _OutcomeEvaluationError("invalid_outcome_role")

    return MeetingOutcome(
        meeting_run_id=session.meeting_run_id,
        status=status,
        summary=str(payload.get("summary", "")).strip(),
        agreements=_string_tuple(payload.get("agreements")),
        disagreements=_string_tuple(payload.get("disagreements")),
        unresolved_roles=unresolved_roles,
        action_items=_string_tuple(payload.get("action_items")),
        evidence_refs=evidence_refs,
        validator_notes=_string_tuple(payload.get("validator_notes")),
        evaluator_role=evaluator_role,
        resolution_kind=resolution_kind,
        generation_status="live",
        model=model,
        created_at=datetime.now(UTC).isoformat(),
    )


def _apply_live_evidence_gate(
    session: MultiBotSession,
    outcome: MeetingOutcome,
) -> MeetingOutcome:
    live_roles_by_round = []
    for round_data in session.rounds:
        live_roles_by_round.append(
            {
                message.bot_role
                for message in round_data.messages
                if message.generation_status == "live"
            }
        )
    live_in_every_round = (
        set.intersection(*live_roles_by_round[:2])
        if len(live_roles_by_round) >= 2
        else set()
    )
    if outcome.status == MeetingOutcomeStatus.AGREED and not _VISIBLE_ROLES.issubset(
        live_in_every_round
    ):
        return _failed_outcome(
            session.meeting_run_id,
            model=outcome.model,
            error_code="insufficient_live_evidence",
            summary=outcome.summary,
            evaluator_role=outcome.evaluator_role,
            resolution_kind=outcome.resolution_kind,
        )
    if outcome.status in {
        MeetingOutcomeStatus.PARTIAL_AGREEMENT,
        MeetingOutcomeStatus.BLOCKED,
    } and (
        len(live_in_every_round & _VISIBLE_ROLES) < 4
        or "validation_audit" not in live_in_every_round
    ):
        return _failed_outcome(
            session.meeting_run_id,
            model=outcome.model,
            error_code="insufficient_live_evidence",
            summary=outcome.summary,
            evaluator_role=outcome.evaluator_role,
            resolution_kind=outcome.resolution_kind,
        )
    for round_data in session.rounds[2:]:
        if not round_data.messages or any(
            message.generation_status != "live" for message in round_data.messages
        ):
            return _failed_outcome(
                session.meeting_run_id,
                model=outcome.model,
                error_code="insufficient_live_evidence",
                summary=outcome.summary,
                evaluator_role=outcome.evaluator_role,
                resolution_kind=outcome.resolution_kind,
            )
        round_roles = {message.bot_role for message in round_data.messages}
        if "validation_audit" not in round_roles:
            return _failed_outcome(
                session.meeting_run_id,
                model=outcome.model,
                error_code="missing_convergence_validator",
                summary=outcome.summary,
                evaluator_role=outcome.evaluator_role,
                resolution_kind=outcome.resolution_kind,
            )
    if session.rounds and len(session.rounds) > 2:
        latest_prefix = f"round:{session.rounds[-1].round_number}:"
        if not any(
            reference.startswith(latest_prefix) for reference in outcome.evidence_refs
        ):
            return _failed_outcome(
                session.meeting_run_id,
                model=outcome.model,
                error_code="missing_latest_round_evidence",
                summary=outcome.summary,
                evaluator_role=outcome.evaluator_role,
                resolution_kind=outcome.resolution_kind,
            )
    return outcome


def _failed_outcome(
    meeting_run_id: str,
    *,
    model: str,
    error_code: str,
    summary: str = "회의 결과를 확정하지 못했습니다.",
    evaluator_role: str = "validation_audit",
    resolution_kind: str = "validation",
) -> MeetingOutcome:
    return MeetingOutcome(
        meeting_run_id=meeting_run_id,
        status=MeetingOutcomeStatus.NEEDS_USER_DECISION,
        summary=summary,
        evaluator_role=evaluator_role,
        resolution_kind=resolution_kind,
        generation_status="failed",
        model=model,
        error_code=error_code,
        created_at=datetime.now(UTC).isoformat(),
    )


def _string_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise _OutcomeEvaluationError("invalid_outcome_json")
    return tuple(str(item).strip() for item in value if str(item).strip())


__all__ = ["evaluate_meeting_outcome"]
