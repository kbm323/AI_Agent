"""Evidence-based outcome evaluation for Runtime Architecture v2 meetings."""

from __future__ import annotations

import json
from dataclasses import dataclass
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
) -> MeetingOutcome:
    """Evaluate a persisted transcript and fail closed on unproven agreement."""

    policy = worker_model_policy_for_role("validation_audit")
    model = str(policy.get("preferred") or policy.get("primary_model") or "glm-5.1")
    prompt = _build_outcome_prompt(session)
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
        outcome = _outcome_from_payload(session, payload, model=model)
        return _apply_live_evidence_gate(session, outcome)
    except _OutcomeEvaluationError as exc:
        return _failed_outcome(session.meeting_run_id, model=model, error_code=exc.code)
    except Exception:
        return _failed_outcome(
            session.meeting_run_id,
            model=model,
            error_code="outcome_provider_error",
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


def _build_outcome_prompt(session: MultiBotSession) -> str:
    transcript_lines = []
    for round_data in session.rounds:
        for message in round_data.messages:
            evidence_ref = f"round:{round_data.round_number}:{message.bot_role}"
            transcript_lines.append(
                f"[{evidence_ref}] status={message.generation_status} {message.content}"
            )
    transcript = "\n".join(transcript_lines)
    return (
        "당신은 회의 결과를 검증하는 품질관리 책임자입니다.\n"
        "아래 회의록만 근거로 결과를 JSON 객체 하나로 반환하세요.\n"
        "status는 agreed, partial_agreement, blocked, needs_user_decision 중 하나입니다.\n"
        "필수 키: status, summary, agreements, disagreements, action_items, "
        "evidence_refs, validator_notes.\n"
        "evidence_refs에는 제공된 round:<번호>:<역할> 식별자만 사용하세요.\n"
        "근거가 부족하면 needs_user_decision을 선택하세요.\n\n"
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

    return MeetingOutcome(
        meeting_run_id=session.meeting_run_id,
        status=status,
        summary=str(payload.get("summary", "")).strip(),
        agreements=_string_tuple(payload.get("agreements")),
        disagreements=_string_tuple(payload.get("disagreements")),
        action_items=_string_tuple(payload.get("action_items")),
        evidence_refs=evidence_refs,
        validator_notes=_string_tuple(payload.get("validator_notes")),
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
        set.intersection(*live_roles_by_round) if live_roles_by_round else set()
    )
    if outcome.status == MeetingOutcomeStatus.AGREED and not _VISIBLE_ROLES.issubset(
        live_in_every_round
    ):
        return _failed_outcome(
            session.meeting_run_id,
            model=outcome.model,
            error_code="insufficient_live_evidence",
            summary=outcome.summary,
        )
    if outcome.status == MeetingOutcomeStatus.PARTIAL_AGREEMENT and (
        len(live_in_every_round & _VISIBLE_ROLES) < 4
        or "validation_audit" not in live_in_every_round
    ):
        return _failed_outcome(
            session.meeting_run_id,
            model=outcome.model,
            error_code="insufficient_live_evidence",
            summary=outcome.summary,
        )
    return outcome


def _failed_outcome(
    meeting_run_id: str,
    *,
    model: str,
    error_code: str,
    summary: str = "회의 결과를 확정하지 못했습니다.",
) -> MeetingOutcome:
    return MeetingOutcome(
        meeting_run_id=meeting_run_id,
        status=MeetingOutcomeStatus.NEEDS_USER_DECISION,
        summary=summary,
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
