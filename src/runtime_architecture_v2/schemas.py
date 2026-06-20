"""Domain schemas for Runtime Architecture v2.

The schemas in this module model AI_Agent-owned MeetingRun artifacts only.
Hermes-native resources are referenced by ID in ``hermes_refs`` and are not
copied into the project-local source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, cast


class MeetingRunState(StrEnum):
    CREATED = "created"
    CLASSIFIED = "classified"
    ROUTED = "routed"
    QUEUED = "queued"
    ACTIVE = "active"
    VALIDATING = "validating"
    REPORTING = "reporting"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PAUSED = "paused"


class WorkerTaskState(StrEnum):
    CREATED = "created"
    PACKET_WRITTEN = "packet_written"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PAUSED = "paused"


class WorkerTaskRunner(StrEnum):
    OPENCODE_GO = "opencode_go"
    HERMES_WRAPPER = "hermes_wrapper"


class ValidationVerdictValue(StrEnum):
    PASS = "pass"
    REVISE = "revise"
    FAIL = "fail"
    DEGRADED = "degraded"


_VALID_PRIORITIES = {"P0", "P1", "P2", "P3"}
_RESEARCH_FORBIDDEN_TEAMS = {"research_lead", "research_intelligence_lead"}
_TERMINAL_STATES = {
    MeetingRunState.COMPLETED,
    MeetingRunState.FAILED,
    MeetingRunState.CANCELLED,
}


def _enum_value(enum_type: type[StrEnum], value: StrEnum | str, label: str) -> StrEnum:
    try:
        return value if isinstance(value, enum_type) else enum_type(str(value))
    except ValueError as exc:
        raise ValueError(f"invalid {label}: {value}") from exc


def _enum_text(value: StrEnum | str) -> str:
    return cast(StrEnum, value).value if isinstance(value, StrEnum) else str(value)


def _tuple(value: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
    return tuple(value or ())


def _dict(value: dict[str, Any] | None) -> dict[str, Any]:
    return dict(value or {})


@dataclass(frozen=True)
class MeetingRun:
    """Root aggregate for AI_Agent meeting/runtime work."""

    meeting_run_id: str
    state: MeetingRunState | str = MeetingRunState.CREATED
    trigger: dict[str, Any] = field(default_factory=dict)
    priority: str = "P2"
    hermes_refs: dict[str, str] = field(default_factory=dict)
    routing_result: dict[str, Any] | None = None
    worker_task_ids: tuple[str, ...] = ()
    validation_ids: tuple[str, ...] = ()
    projection_event_ids: tuple[str, ...] = ()
    checkpoint_ids: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        state = _enum_value(MeetingRunState, self.state, "MeetingRun state")
        if self.priority not in _VALID_PRIORITIES:
            raise ValueError(f"invalid priority: {self.priority}")
        object.__setattr__(self, "state", state)
        object.__setattr__(self, "hermes_refs", _dict(self.hermes_refs))
        object.__setattr__(self, "worker_task_ids", _tuple(self.worker_task_ids))
        object.__setattr__(self, "validation_ids", _tuple(self.validation_ids))
        object.__setattr__(self, "projection_event_ids", _tuple(self.projection_event_ids))
        object.__setattr__(self, "checkpoint_ids", _tuple(self.checkpoint_ids))
        object.__setattr__(self, "metadata", _dict(self.metadata))

    @classmethod
    def create(
        cls,
        *,
        meeting_run_id: str,
        trigger_text: str,
        user_id: str,
        channel_id: str,
        thread_id: str,
        guild_id: str = "",
        hermes_session_id: str = "",
        priority: str = "P2",
    ) -> "MeetingRun":
        hermes_refs = {"session_id": hermes_session_id} if hermes_session_id else {}
        return cls(
            meeting_run_id=meeting_run_id,
            state=MeetingRunState.CREATED,
            trigger={
                "text": trigger_text,
                "user_id": user_id,
                "discord": {
                    "channel_id": channel_id,
                    "thread_id": thread_id,
                    "guild_id": guild_id,
                },
            },
            priority=priority,
            hermes_refs=hermes_refs,
        )

    @property
    def is_terminal(self) -> bool:
        return self.state in _TERMINAL_STATES

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "meeting_run_id": self.meeting_run_id,
            "state": _enum_text(self.state),
            "trigger": self.trigger,
            "priority": self.priority,
            "hermes_refs": self.hermes_refs,
            "worker_task_ids": list(self.worker_task_ids),
            "validation_ids": list(self.validation_ids),
            "projection_event_ids": list(self.projection_event_ids),
            "checkpoint_ids": list(self.checkpoint_ids),
            "metadata": self.metadata,
        }
        if self.routing_result is not None:
            payload["routing_result"] = self.routing_result
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "MeetingRun":
        return cls(
            meeting_run_id=payload["meeting_run_id"],
            state=payload.get("state", MeetingRunState.CREATED),
            trigger=dict(payload.get("trigger") or {}),
            priority=payload.get("priority", "P2"),
            hermes_refs=dict(payload.get("hermes_refs") or {}),
            routing_result=payload.get("routing_result"),
            worker_task_ids=tuple(payload.get("worker_task_ids") or ()),
            validation_ids=tuple(payload.get("validation_ids") or ()),
            projection_event_ids=tuple(payload.get("projection_event_ids") or ()),
            checkpoint_ids=tuple(payload.get("checkpoint_ids") or ()),
            metadata=dict(payload.get("metadata") or {}),
        )


@dataclass(frozen=True)
class RoutingResult:
    """Domain routing output for one MeetingRun."""

    meeting_run_id: str
    route_type: str
    teams: tuple[str, ...] = ()
    worker_roles: tuple[str, ...] = ()
    validators: tuple[str, ...] = ()
    research_owner: str = ""
    execution_required: bool = False
    estimated_rounds: int = 1
    projection_policy: str = "summary_only"
    confidence: float = 1.0
    rationale: str = ""

    def __post_init__(self) -> None:
        teams = _tuple(self.teams)
        worker_roles = _tuple(self.worker_roles)
        validators = _tuple(self.validators)
        if self.research_owner in _RESEARCH_FORBIDDEN_TEAMS or any(
            team in _RESEARCH_FORBIDDEN_TEAMS for team in teams
        ):
            raise ValueError("Research must be delegated to an existing domain team")
        if not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        object.__setattr__(self, "teams", teams)
        object.__setattr__(self, "worker_roles", worker_roles)
        object.__setattr__(self, "validators", validators)

    def to_dict(self) -> dict[str, Any]:
        return {
            "meeting_run_id": self.meeting_run_id,
            "route_type": self.route_type,
            "teams": list(self.teams),
            "worker_roles": list(self.worker_roles),
            "validators": list(self.validators),
            "research_owner": self.research_owner,
            "execution_required": self.execution_required,
            "estimated_rounds": self.estimated_rounds,
            "projection_policy": self.projection_policy,
            "confidence": self.confidence,
            "rationale": self.rationale,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RoutingResult":
        return cls(
            meeting_run_id=payload["meeting_run_id"],
            route_type=payload["route_type"],
            teams=tuple(payload.get("teams") or ()),
            worker_roles=tuple(payload.get("worker_roles") or ()),
            validators=tuple(payload.get("validators") or ()),
            research_owner=payload.get("research_owner", ""),
            execution_required=bool(payload.get("execution_required", False)),
            estimated_rounds=int(payload.get("estimated_rounds", 1)),
            projection_policy=payload.get("projection_policy", "summary_only"),
            confidence=float(payload.get("confidence", 1.0)),
            rationale=payload.get("rationale", ""),
        )


@dataclass(frozen=True)
class WorkerTask:
    """Executable worker/validator/auditor packet reference."""

    worker_task_id: str
    meeting_run_id: str
    role: str
    runner: WorkerTaskRunner | str
    state: WorkerTaskState | str = WorkerTaskState.CREATED
    model_policy: dict[str, Any] = field(default_factory=dict)
    packet_path: str = ""
    output_path: str = ""
    error: str = ""
    hermes_refs: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        runner = _enum_value(WorkerTaskRunner, self.runner, "WorkerTask runner")
        state = _enum_value(WorkerTaskState, self.state, "WorkerTask state")
        object.__setattr__(self, "runner", runner)
        object.__setattr__(self, "state", state)
        object.__setattr__(self, "model_policy", _dict(self.model_policy))
        object.__setattr__(self, "hermes_refs", _dict(self.hermes_refs))

    def to_dict(self) -> dict[str, Any]:
        return {
            "worker_task_id": self.worker_task_id,
            "meeting_run_id": self.meeting_run_id,
            "role": self.role,
            "runner": _enum_text(self.runner),
            "state": _enum_text(self.state),
            "model_policy": self.model_policy,
            "packet_path": self.packet_path,
            "output_path": self.output_path,
            "error": self.error,
            "hermes_refs": self.hermes_refs,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "WorkerTask":
        return cls(
            worker_task_id=payload["worker_task_id"],
            meeting_run_id=payload["meeting_run_id"],
            role=payload["role"],
            runner=payload["runner"],
            state=payload.get("state", WorkerTaskState.CREATED),
            model_policy=dict(payload.get("model_policy") or {}),
            packet_path=payload.get("packet_path", ""),
            output_path=payload.get("output_path", ""),
            error=payload.get("error", ""),
            hermes_refs=dict(payload.get("hermes_refs") or {}),
        )


@dataclass(frozen=True)
class ValidationVerdict:
    """Explicit validation/audit result."""

    validation_id: str
    meeting_run_id: str
    validator_role: str
    validator_model: str
    verdict: ValidationVerdictValue | str
    confidence: float
    findings: tuple[str, ...] = ()
    required_actions: tuple[str, ...] = ()
    degraded_reason: str = ""

    def __post_init__(self) -> None:
        verdict = _enum_value(ValidationVerdictValue, self.verdict, "verdict")
        if not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        object.__setattr__(self, "verdict", verdict)
        object.__setattr__(self, "findings", _tuple(self.findings))
        object.__setattr__(self, "required_actions", _tuple(self.required_actions))

    def to_dict(self) -> dict[str, Any]:
        return {
            "validation_id": self.validation_id,
            "meeting_run_id": self.meeting_run_id,
            "validator_role": self.validator_role,
            "validator_model": self.validator_model,
            "verdict": _enum_text(self.verdict),
            "confidence": self.confidence,
            "findings": list(self.findings),
            "required_actions": list(self.required_actions),
            "degraded_reason": self.degraded_reason,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ValidationVerdict":
        return cls(
            validation_id=payload["validation_id"],
            meeting_run_id=payload["meeting_run_id"],
            validator_role=payload["validator_role"],
            validator_model=payload["validator_model"],
            verdict=payload["verdict"],
            confidence=float(payload["confidence"]),
            findings=tuple(payload.get("findings") or ()),
            required_actions=tuple(payload.get("required_actions") or ()),
            degraded_reason=payload.get("degraded_reason", ""),
        )


@dataclass(frozen=True)
class DiscordProjectionEvent:
    """User-facing Discord projection event."""

    event_id: str
    meeting_run_id: str
    bot_role: str
    target_channel_id: str
    content: str
    source: str
    source_id: str
    target_thread_id: str = ""
    visibility: str = "public"

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "meeting_run_id": self.meeting_run_id,
            "bot_role": self.bot_role,
            "target_channel_id": self.target_channel_id,
            "target_thread_id": self.target_thread_id,
            "content": self.content,
            "source": self.source,
            "source_id": self.source_id,
            "visibility": self.visibility,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "DiscordProjectionEvent":
        return cls(
            event_id=payload["event_id"],
            meeting_run_id=payload["meeting_run_id"],
            bot_role=payload["bot_role"],
            target_channel_id=payload["target_channel_id"],
            target_thread_id=payload.get("target_thread_id", ""),
            content=payload["content"],
            source=payload["source"],
            source_id=payload["source_id"],
            visibility=payload.get("visibility", "public"),
        )


@dataclass(frozen=True)
class RecoveryCheckpoint:
    """Checkpoint for idempotent MeetingRun recovery."""

    checkpoint_id: str
    meeting_run_id: str
    state: MeetingRunState | str
    completed_worker_task_ids: tuple[str, ...] = ()
    pending_worker_task_ids: tuple[str, ...] = ()
    hermes_refs: dict[str, str] = field(default_factory=dict)
    checkpoint_path: str = ""
    idempotency_key: str = ""
    replay_token: str = ""
    note: str = ""

    def __post_init__(self) -> None:
        state = _enum_value(MeetingRunState, self.state, "MeetingRun state")
        object.__setattr__(self, "state", state)
        object.__setattr__(
            self, "completed_worker_task_ids", _tuple(self.completed_worker_task_ids)
        )
        object.__setattr__(
            self, "pending_worker_task_ids", _tuple(self.pending_worker_task_ids)
        )
        object.__setattr__(self, "hermes_refs", _dict(self.hermes_refs))

    def to_dict(self) -> dict[str, Any]:
        return {
            "checkpoint_id": self.checkpoint_id,
            "meeting_run_id": self.meeting_run_id,
            "state": _enum_text(self.state),
            "completed_worker_task_ids": list(self.completed_worker_task_ids),
            "pending_worker_task_ids": list(self.pending_worker_task_ids),
            "hermes_refs": self.hermes_refs,
            "checkpoint_path": self.checkpoint_path,
            "idempotency_key": self.idempotency_key,
            "replay_token": self.replay_token,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RecoveryCheckpoint":
        return cls(
            checkpoint_id=payload["checkpoint_id"],
            meeting_run_id=payload["meeting_run_id"],
            state=payload["state"],
            completed_worker_task_ids=tuple(
                payload.get("completed_worker_task_ids") or ()
            ),
            pending_worker_task_ids=tuple(payload.get("pending_worker_task_ids") or ()),
            hermes_refs=dict(payload.get("hermes_refs") or {}),
            checkpoint_path=payload.get("checkpoint_path", ""),
            idempotency_key=payload.get("idempotency_key", ""),
            replay_token=payload.get("replay_token", ""),
            note=payload.get("note", ""),
        )


__all__ = [
    "DiscordProjectionEvent",
    "MeetingRun",
    "MeetingRunState",
    "RecoveryCheckpoint",
    "RoutingResult",
    "ValidationVerdict",
    "ValidationVerdictValue",
    "WorkerTask",
    "WorkerTaskRunner",
    "WorkerTaskState",
]
