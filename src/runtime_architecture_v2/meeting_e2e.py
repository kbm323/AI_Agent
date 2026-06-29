"""Phase 30 GPT-only MeetingRun E2E skeleton.

This module intentionally avoids opencode-go. It builds the deterministic
orchestration, artifact, projection, and knowledge-writing path so live worker
providers can be swapped in later without changing the MeetingRun contract.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, cast

from .knowledge import KnowledgeWriteResult, write_meeting_knowledge
from .schemas import MeetingRun, MeetingRunState, RecoveryCheckpoint
from .store import MeetingRunStore

DEFAULT_PHASE30_ROLES: tuple[str, ...] = (
    "assistant_secretary",
    "ceo_coordinator",
    "content_lead",
    "art_lead",
    "tech_lead",
    "marketing_lead",
    "quality_lead",
)

_STAGE_SEQUENCE: tuple[str, ...] = (
    "intake_normalized",
    "meeting_run_created",
    "thread_created",
    "opinions_collected",
    "rebuttals_collected",
    "consensus_derived",
    "validation_packet_written",
    "final_report_written",
    "evidence_written",
    "recovery_checkpoint_written",
)

_SECRET_RE = re.compile(
    r"(?i)(api[_-]?key|secret|password|passwd|token)\s*[:=]\s*[^\s`'\"]+"
)
_BEARER_RE = re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{6,}")


@dataclass(frozen=True)
class Phase30Trigger:
    """Normalized user/Hermes/Discord trigger for a Phase 30 meeting."""

    trigger_text: str
    user_id: str = "phase30-user"
    channel_id: str = "phase30-channel"
    thread_id: str = ""
    guild_id: str = ""
    profile: str = "aicompanyceo"
    hermes_session_id: str = ""
    priority: str = "P1"


@dataclass(frozen=True)
class Phase30RoleMessage:
    """One role-visible message in a Phase 30 meeting round."""

    role: str
    round_name: str
    content: str

    def to_dict(self) -> dict[str, str]:
        return {"role": self.role, "round": self.round_name, "content": self.content}


@dataclass(frozen=True)
class Phase30MeetingRound:
    """One named round containing every role's message."""

    round_name: str
    messages: tuple[Phase30RoleMessage, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "round": self.round_name,
            "messages": [message.to_dict() for message in self.messages],
        }


@dataclass(frozen=True)
class Phase30Session:
    """Knowledge-writer-compatible session shape for Phase 30."""

    meeting_run_id: str
    participants: tuple[str, ...]
    rounds: tuple[Phase30MeetingRound, ...]
    consensus_reached: bool
    escalation_required: bool
    consensus_summary: str

    def to_dict(self) -> dict[str, object]:
        return {
            "meeting_run_id": self.meeting_run_id,
            "participants": list(self.participants),
            "rounds": [round_data.to_dict() for round_data in self.rounds],
            "consensus_reached": self.consensus_reached,
            "escalation_required": self.escalation_required,
            "consensus_summary": self.consensus_summary,
        }


@dataclass(frozen=True)
class Phase30ConsensusPacket:
    """Deterministic consensus result, later replaceable by GLM validation."""

    consensus_reached: bool
    escalation_required: bool
    blockers: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    summary: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "consensus_reached": self.consensus_reached,
            "escalation_required": self.escalation_required,
            "blockers": list(self.blockers),
            "conflicts": list(self.conflicts),
            "summary": self.summary,
        }


@dataclass(frozen=True)
class MeetingValidationResult:
    """Sanitized result from deterministic or live meeting validation."""

    status: str
    provider: str
    model: str
    verdict: str
    confidence: float
    summary: str
    blockers: tuple[str, ...] = ()
    raw_output: str = ""

    @property
    def blocks_release(self) -> bool:
        return self.status != "ok" or self.verdict in {"block", "fail"} or bool(
            self.blockers
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "provider": self.provider,
            "model": self.model,
            "verdict": self.verdict,
            "confidence": self.confidence,
            "summary": _sanitize_text(self.summary),
            "blockers": [_sanitize_text(blocker) for blocker in self.blockers],
        }


@dataclass(frozen=True)
class MeetingAuditResult:
    """Sanitized result from Codex/GPT final audit escalation."""

    status: str
    provider: str
    model: str
    verdict: str
    summary: str
    findings: tuple[str, ...] = ()
    raw_output: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "provider": self.provider,
            "model": self.model,
            "verdict": self.verdict,
            "summary": _sanitize_text(self.summary),
            "findings": [_sanitize_text(finding) for finding in self.findings],
        }


@dataclass(frozen=True)
class Phase30ValidationPacket:
    """Validator input packet for later GLM/Codex runtime replacement."""

    meeting_run_id: str
    roles: tuple[str, ...]
    consensus: Phase30ConsensusPacket
    opencode_used: bool = False
    validator_model: str = "deterministic-gpt-only-validator"
    validation_result: MeetingValidationResult | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "meeting_run_id": self.meeting_run_id,
            "roles": list(self.roles),
            "consensus": self.consensus.to_dict(),
            "opencode_used": self.opencode_used,
            "validator_model": self.validator_model,
            "validation_result": (
                self.validation_result.to_dict() if self.validation_result else None
            ),
        }


class MeetingValidationRunner(Protocol):
    """Callable boundary for GLM/live meeting validation."""

    def __call__(self, packet: Phase30ValidationPacket) -> MeetingValidationResult:
        """Validate a meeting packet and return a structured verdict."""
        ...


class MeetingAuditRunner(Protocol):
    """Callable boundary for Codex/GPT final audit escalation."""

    def __call__(
        self,
        packet: Phase30ValidationPacket,
        *,
        validation_result: MeetingValidationResult | None,
    ) -> MeetingAuditResult:
        """Audit a high-risk or blocked meeting packet."""
        ...


@dataclass(frozen=True)
class Phase30ProjectionResult:
    """Result of injected thread projection."""

    thread_status: str
    thread_id: str
    message_ids: tuple[str, ...]
    error: str = ""

    @property
    def posted_count(self) -> int:
        return len(self.message_ids)

    def to_dict(self) -> dict[str, object]:
        return {
            "thread_status": self.thread_status,
            "thread_id": self.thread_id,
            "message_ids": list(self.message_ids),
            "posted_count": self.posted_count,
            "error": self.error,
        }


@dataclass(frozen=True)
class Phase30MeetingE2EResult:
    """Top-level Phase 30 E2E result."""

    ok: bool
    meeting_run: MeetingRun
    roles: tuple[str, ...]
    rounds: tuple[Phase30MeetingRound, ...]
    consensus: Phase30ConsensusPacket
    validation_packet: Phase30ValidationPacket
    projection: Phase30ProjectionResult
    final_report_path: str
    evidence_path: str
    recovery_checkpoint_path: str
    stage_sequence: tuple[str, ...]
    audit_result: MeetingAuditResult | None = None
    knowledge: KnowledgeWriteResult | None = None
    error: str = ""

    @property
    def meeting_run_id(self) -> str:
        return self.meeting_run.meeting_run_id

    def to_dict(self) -> dict[str, object]:
        state = cast(MeetingRunState, self.meeting_run.state)
        return {
            "ok": self.ok,
            "meeting_run_id": self.meeting_run.meeting_run_id,
            "state": str(state.value),
            "roles": list(self.roles),
            "rounds": [round_data.to_dict() for round_data in self.rounds],
            "consensus": self.consensus.to_dict(),
            "validation_packet": self.validation_packet.to_dict(),
            "projection": self.projection.to_dict(),
            "final_report_path": self.final_report_path,
            "evidence_path": self.evidence_path,
            "recovery_checkpoint_path": self.recovery_checkpoint_path,
            "stage_sequence": list(self.stage_sequence),
            "audit_result": self.audit_result.to_dict() if self.audit_result else None,
            "knowledge": self.knowledge.to_dict() if self.knowledge else None,
            "error": self.error,
            "opencode_used": self.validation_packet.opencode_used,
        }


class RoleOutputProvider(Protocol):
    """Boundary for role text generation."""

    def generate(self, *, role: str, round_name: str, trigger: Phase30Trigger) -> str:
        """Return role output for one round."""
        ...


@dataclass(frozen=True)
class OpenCodeGoCallResult:
    """Sanitized provenance for one opencode-go role call."""

    role: str
    round_name: str
    status: str
    provider: str
    model: str
    executable: str
    exit_code: int
    duration_sec: float
    timed_out: bool = False
    stderr: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "role": self.role,
            "round": self.round_name,
            "status": self.status,
            "provider": self.provider,
            "model": self.model,
            "executable": self.executable,
            "exit_code": self.exit_code,
            "duration_sec": self.duration_sec,
            "timed_out": self.timed_out,
            "stderr": self.stderr,
        }


class OpenCodeGoRunner(Protocol):
    """Callable subprocess boundary for opencode-go."""

    def __call__(
        self,
        argv: list[str],
        *,
        input_text: str,
        timeout_sec: int,
        env: dict[str, str],
    ) -> dict[str, object]:
        """Run opencode-go and return a subprocess-like result mapping."""
        ...


@dataclass
class OpenCodeGoRoleOutputProvider:
    """RoleOutputProvider backed by opencode-go with injectable runner."""

    runner: OpenCodeGoRunner | None = None
    executable: str = "opencode-go"
    model: str = "glm-5.2"
    timeout_sec: int = 120
    env: dict[str, str] = field(default_factory=dict)
    last_results: list[OpenCodeGoCallResult] = field(default_factory=list)

    def generate(self, *, role: str, round_name: str, trigger: Phase30Trigger) -> str:
        prompt = _build_opencode_role_prompt(
            role=role,
            round_name=round_name,
            trigger=trigger,
        )
        context_path = _write_opencode_context_file(prompt)
        argv = [
            self.executable,
            "--model",
            self.model,
            "--context-file",
            str(context_path),
            "--timeout-seconds",
            str(self.timeout_sec),
            "--prompt",
            prompt,
            "--format",
            "json",
        ]
        started = time.monotonic()
        runner = self.runner or _default_opencode_go_runner
        try:
            result = runner(
                argv,
                input_text=prompt,
                timeout_sec=self.timeout_sec,
                env={**os.environ, **self.env},
            )
        finally:
            context_path.unlink(missing_ok=True)
        duration = _coerce_float(result.get("duration_sec"), time.monotonic() - started)
        exit_code = _coerce_int(result.get("returncode"), 1)
        timed_out = bool(result.get("timed_out", False))
        stderr = _sanitize_text(str(result.get("stderr") or ""))
        stdout = str(result.get("stdout") or "")

        if exit_code != 0 or timed_out:
            self.last_results.append(
                OpenCodeGoCallResult(
                    role=role,
                    round_name=round_name,
                    status="failed",
                    provider="opencode-go",
                    model=self.model,
                    executable=self.executable,
                    exit_code=exit_code,
                    duration_sec=duration,
                    timed_out=timed_out,
                    stderr=stderr,
                )
            )
            return "BLOCKER: opencode-go role output failed"

        content = _extract_opencode_content(stdout)
        self.last_results.append(
            OpenCodeGoCallResult(
                role=role,
                round_name=round_name,
                status="ok",
                provider="opencode-go",
                model=self.model,
                executable=self.executable,
                exit_code=exit_code,
                duration_sec=duration,
                timed_out=False,
                stderr=stderr,
            )
        )
        return content


@dataclass(frozen=True)
class InjectedRoleOutputProvider:
    """Deterministic provider for GPT-only tests and dry-runs."""

    outputs: dict[str, dict[str, str]] = field(default_factory=dict)

    def generate(self, *, role: str, round_name: str, trigger: Phase30Trigger) -> str:
        role_outputs = self.outputs.get(role, {})
        if round_name in role_outputs:
            return _sanitize_text(role_outputs[round_name])
        return _sanitize_text(
            f"{role} {round_name}: {trigger.trigger_text} 안건에 대한 "
            "deterministic 의견입니다."
        )


class MeetingThreadProjectionAdapter(Protocol):
    """Injected projection boundary for thread/message publishing."""

    def create_thread(self, *, meeting_run_id: str, trigger: Phase30Trigger) -> str:
        """Create or return a shared thread ID."""
        ...

    def post_message(self, *, thread_id: str, role: str, content: str) -> str:
        """Post a role/final report message and return message ID."""
        ...


class DiscordHttpClient(Protocol):
    """Injected HTTP boundary for Discord REST calls."""

    def __call__(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        json_body: dict[str, object],
        timeout_sec: int,
    ) -> dict[str, object]:
        """Return a mapping with status and json keys."""
        ...


@dataclass
class FakeMeetingThreadProjectionAdapter:
    """In-memory thread projection adapter for dry-run verification."""

    thread_prefix: str = "dry-run-thread"
    messages: list[dict[str, str]] = field(default_factory=list)
    created_threads: list[str] = field(default_factory=list)

    def create_thread(self, *, meeting_run_id: str, trigger: Phase30Trigger) -> str:
        del trigger
        thread_id = f"{self.thread_prefix}-{meeting_run_id}"
        self.created_threads.append(thread_id)
        return thread_id

    def post_message(self, *, thread_id: str, role: str, content: str) -> str:
        message_id = f"dry-run-message-{len(self.messages) + 1:02d}"
        self.messages.append(
            {
                "thread_id": thread_id,
                "role": role,
                "content": _sanitize_text(content),
                "message_id": message_id,
            }
        )
        return message_id


@dataclass
class DiscordRestProjectionAdapter:
    """Discord REST projection adapter behind an injected HTTP boundary."""

    bot_token: str
    channel_id: str
    http_client: DiscordHttpClient | None = None
    api_base: str = "https://discord.com/api/v10"
    timeout_sec: int = 20
    user_agent: str = "AI_Agent Phase31 MeetingRun (+https://github.com/kbm323/AI_Agent)"
    last_error: str = ""

    def create_thread(self, *, meeting_run_id: str, trigger: Phase30Trigger) -> str:
        del trigger
        response = self._request(
            "POST",
            f"/channels/{self.channel_id}/threads",
            {
                "name": _sanitize_text(f"meeting-{meeting_run_id}")[:100],
                "auto_archive_duration": 60,
                "type": 11,
            },
        )
        if not _discord_success(response, expected=(200, 201)):
            self.last_error = "discord_thread_create_failed"
            return ""
        thread_id = _response_json_id(response)
        if not thread_id:
            self.last_error = "discord_thread_create_failed"
        return thread_id

    def post_message(self, *, thread_id: str, role: str, content: str) -> str:
        del role
        response = self._request(
            "POST",
            f"/channels/{thread_id}/messages",
            {
                "content": _sanitize_text(content)[:1900],
                "allowed_mentions": {"parse": []},
                "embeds": [],
            },
        )
        if not _discord_success(response, expected=(200, 201)):
            self.last_error = "discord_message_post_failed"
            return ""
        message_id = _response_json_id(response)
        if not message_id:
            self.last_error = "discord_message_post_failed"
        return message_id

    def _request(
        self, method: str, path: str, json_body: dict[str, object]
    ) -> dict[str, object]:
        client = self.http_client or _default_discord_http_client
        return client(
            method,
            f"{self.api_base}{path}",
            headers={
                "Authorization": f"Bot {self.bot_token}",
                "Content-Type": "application/json",
                "User-Agent": self.user_agent,
            },
            json_body=json_body,
            timeout_sec=self.timeout_sec,
        )


def _discord_success(
    response: dict[str, object], *, expected: tuple[int, ...]
) -> bool:
    status = _coerce_int(response.get("status"), 0)
    return status in expected


def _response_json_id(response: dict[str, object]) -> str:
    payload = response.get("json")
    if isinstance(payload, dict):
        value = payload.get("id")
        if isinstance(value, str):
            return value
    return ""


def _default_discord_http_client(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    json_body: dict[str, object],
    timeout_sec: int,
) -> dict[str, object]:
    data = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_sec) as response:
            body = response.read().decode("utf-8")
            return {"status": int(response.status), "json": _try_json(body) or {}}
    except urllib.error.HTTPError as exc:
        return {"status": int(exc.code), "json": {}}
    except Exception:
        return {"status": 0, "json": {}}


def normalize_phase30_gateway_input(payload: dict[str, object]) -> Phase30Trigger:
    """Normalize a Hermes/Discord-like payload into a Phase30Trigger."""

    content = str(payload.get("content") or payload.get("trigger_text") or "").strip()
    if not content:
        raise ValueError("trigger content is required")
    return Phase30Trigger(
        trigger_text=_sanitize_text(content),
        user_id=str(payload.get("user_id") or "phase30-user"),
        channel_id=str(payload.get("channel_id") or "phase30-channel"),
        thread_id=str(payload.get("thread_id") or ""),
        guild_id=str(payload.get("guild_id") or ""),
        profile=str(payload.get("profile") or "aicompanyceo"),
        hermes_session_id=str(payload.get("session_id") or ""),
        priority=str(payload.get("priority") or "P1"),
    )


def run_phase30_meeting_e2e(
    *,
    root: str | Path,
    trigger_text: str | None = None,
    trigger: Phase30Trigger | None = None,
    user_id: str = "phase30-user",
    channel_id: str = "phase30-channel",
    thread_id: str = "",
    guild_id: str = "",
    role_output_provider: RoleOutputProvider | None = None,
    projection_adapter: MeetingThreadProjectionAdapter | None = None,
    validation_runner: MeetingValidationRunner | None = None,
    audit_runner: MeetingAuditRunner | None = None,
    write_knowledge: bool = False,
) -> Phase30MeetingE2EResult:
    """Run a deterministic Phase 30 meeting E2E without opencode-go."""

    root = Path(root)
    normalized = trigger or Phase30Trigger(
        trigger_text=_sanitize_text(trigger_text or "Phase 30 dry-run meeting"),
        user_id=user_id,
        channel_id=channel_id,
        thread_id=thread_id,
        guild_id=guild_id,
    )
    provider = role_output_provider or InjectedRoleOutputProvider()
    adapter = projection_adapter or FakeMeetingThreadProjectionAdapter()
    meeting_run_id = _new_meeting_run_id()
    store = MeetingRunStore(root)
    meeting_run = MeetingRun.create(
        meeting_run_id=meeting_run_id,
        trigger_text=normalized.trigger_text,
        user_id=normalized.user_id,
        channel_id=normalized.channel_id,
        thread_id=normalized.thread_id,
        guild_id=normalized.guild_id,
        hermes_session_id=normalized.hermes_session_id,
        priority=normalized.priority,
    )
    meeting_run = replace(
        meeting_run,
        state=MeetingRunState.ACTIVE,
        metadata={
            "phase": "phase30",
            "profile": normalized.profile,
            "roles": list(DEFAULT_PHASE30_ROLES),
            "opencode_used": False,
        },
    )
    store.save_meeting_run(meeting_run)

    rounds = _collect_rounds(provider=provider, trigger=normalized)
    opencode_results = _opencode_results(provider)
    opencode_used = bool(opencode_results)
    consensus = derive_consensus_packet(rounds)
    validation_packet = Phase30ValidationPacket(
        meeting_run_id=meeting_run_id,
        roles=DEFAULT_PHASE30_ROLES,
        consensus=consensus,
        opencode_used=opencode_used,
    )
    validation_result = _run_validation(
        validation_runner=validation_runner,
        validation_packet=validation_packet,
    )
    if validation_result:
        validation_packet = replace(
            validation_packet,
            validator_model=validation_result.model,
            validation_result=validation_result,
        )
    if validation_result and validation_result.blocks_release:
        consensus = replace(
            consensus,
            escalation_required=True,
            blockers=(*consensus.blockers, *validation_result.blockers),
        )
        validation_packet = replace(validation_packet, consensus=consensus)
    audit_result = _run_audit(
        audit_runner=audit_runner,
        validation_packet=validation_packet,
        validation_result=validation_result,
    )
    final_state = (
        MeetingRunState.COMPLETED
        if consensus.consensus_reached
        and not (validation_result and validation_result.blocks_release)
        else MeetingRunState.FAILED
    )
    error = ""
    if not consensus.consensus_reached:
        error = "consensus_blocked"
    if validation_result and validation_result.blocks_release:
        error = "validation_blocked"

    final_report = _build_final_report(
        meeting_run_id=meeting_run_id,
        trigger=normalized,
        rounds=rounds,
        consensus=consensus,
        validation_result=validation_result,
        audit_result=audit_result,
    )
    projection = _project_meeting(
        adapter=adapter,
        meeting_run_id=meeting_run_id,
        trigger=normalized,
        rounds=rounds,
        final_report=final_report,
    )

    meeting_run = replace(
        meeting_run,
        state=final_state,
        projection_event_ids=tuple(projection.message_ids),
        checkpoint_ids=(f"ckpt_{meeting_run_id}_phase30",),
        metadata={
            **meeting_run.metadata,
            "thread_id": projection.thread_id,
            "posted_count": projection.posted_count,
            "consensus_reached": consensus.consensus_reached,
            "escalation_required": consensus.escalation_required,
            "opencode_used": opencode_used,
        },
    )
    store.save_meeting_run(meeting_run)

    phase_dir = store.meeting_run_dir(meeting_run_id) / "phase30"
    phase_dir.mkdir(parents=True, exist_ok=True)
    _write_json(phase_dir / "rounds.json", {"rounds": [r.to_dict() for r in rounds]})
    _write_json(
        phase_dir / "role_outputs.json",
        _role_outputs_payload(rounds, opencode_results=opencode_results),
    )
    _write_json(phase_dir / "validation_packet.json", validation_packet.to_dict())
    _write_json(phase_dir / "consensus.json", consensus.to_dict())
    final_report_path = phase_dir / "final_report.md"
    _write_text(final_report_path, final_report)
    evidence_path = phase_dir / "evidence.json"
    _write_json(
        evidence_path,
        {
            "meeting_run_id": meeting_run_id,
            "thread_id": projection.thread_id,
            "message_ids": list(projection.message_ids),
            "roles": list(DEFAULT_PHASE30_ROLES),
            "stage_sequence": list(_STAGE_SEQUENCE),
            "opencode_used": opencode_used,
            "opencode_results": opencode_results,
            "validation_result": (
                validation_result.to_dict() if validation_result else None
            ),
            "audit_result": audit_result.to_dict() if audit_result else None,
            "ok": final_state == MeetingRunState.COMPLETED,
            "error": error,
        },
    )
    checkpoint = RecoveryCheckpoint(
        checkpoint_id=f"ckpt_{meeting_run_id}_phase30",
        meeting_run_id=meeting_run_id,
        state=final_state,
        hermes_refs=meeting_run.hermes_refs,
        idempotency_key=f"phase30:{meeting_run_id}",
        note="Phase 30 deterministic E2E checkpoint",
    )
    store.save_checkpoint(checkpoint)
    recovery_checkpoint_path = phase_dir / "recovery_checkpoint.json"
    _write_json(recovery_checkpoint_path, checkpoint.to_dict())

    session = Phase30Session(
        meeting_run_id=meeting_run_id,
        participants=DEFAULT_PHASE30_ROLES,
        rounds=rounds,
        consensus_reached=consensus.consensus_reached,
        escalation_required=consensus.escalation_required,
        consensus_summary=consensus.summary,
    )
    knowledge = None
    if write_knowledge:
        knowledge = write_meeting_knowledge(
            root=root,
            meeting_run=meeting_run,
            session=session,  # type: ignore[arg-type]
            phase="phase30",
            tags=("phase30", "ai-company", "meeting-e2e"),
        )

    return Phase30MeetingE2EResult(
        ok=final_state == MeetingRunState.COMPLETED,
        meeting_run=meeting_run,
        roles=DEFAULT_PHASE30_ROLES,
        rounds=rounds,
        consensus=consensus,
        validation_packet=validation_packet,
        projection=projection,
        final_report_path=str(final_report_path),
        evidence_path=str(evidence_path),
        recovery_checkpoint_path=str(recovery_checkpoint_path),
        stage_sequence=_STAGE_SEQUENCE,
        audit_result=audit_result,
        knowledge=knowledge,
        error=error,
    )


def derive_consensus_packet(
    rounds: tuple[Phase30MeetingRound, ...],
) -> Phase30ConsensusPacket:
    """Derive deterministic consensus from role outputs."""

    blockers: list[str] = []
    conflicts: list[str] = []
    for round_data in rounds:
        for message in round_data.messages:
            upper = message.content.upper()
            if "BLOCKER:" in upper:
                blockers.append(
                    _extract_marker(message.role, message.content, "BLOCKER:")
                )
            if "CONFLICT:" in upper:
                conflicts.append(
                    _extract_marker(message.role, message.content, "CONFLICT:")
                )
    consensus_reached = not blockers and not conflicts
    summary = (
        "7개 Discord-facing 역할의 의견과 반박이 정렬되어 합의에 도달했습니다."
        if consensus_reached
        else "차단 이슈 또는 충돌이 있어 합의에 도달하지 못했습니다."
    )
    return Phase30ConsensusPacket(
        consensus_reached=consensus_reached,
        escalation_required=not consensus_reached,
        blockers=tuple(blockers),
        conflicts=tuple(conflicts),
        summary=summary,
    )


def inspect_phase30_meeting(root: str | Path, meeting_run_id: str) -> dict[str, object]:
    """Inspect persisted Phase 30 artifacts without live services."""

    store = MeetingRunStore(root)
    run = store.load_meeting_run(meeting_run_id)
    state = cast(MeetingRunState, run.state)
    phase_dir = store.meeting_run_dir(meeting_run_id) / "phase30"
    expected = (
        "rounds.json",
        "role_outputs.json",
        "validation_packet.json",
        "consensus.json",
        "final_report.md",
        "evidence.json",
        "recovery_checkpoint.json",
    )
    missing = [name for name in expected if not (phase_dir / name).exists()]
    artifact_paths = [
        f"phase30/{name}" for name in expected if (phase_dir / name).exists()
    ]
    return {
        "ok": not missing,
        "meeting_run_id": meeting_run_id,
        "state": str(state.value),
        "missing_files": missing,
        "artifact_paths": artifact_paths,
    }


def _collect_rounds(
    *, provider: RoleOutputProvider, trigger: Phase30Trigger
) -> tuple[Phase30MeetingRound, ...]:
    rounds: list[Phase30MeetingRound] = []
    for round_name in ("opinion", "rebuttal"):
        messages = tuple(
            Phase30RoleMessage(
                role=role,
                round_name=round_name,
                content=provider.generate(
                    role=role, round_name=round_name, trigger=trigger
                ),
            )
            for role in DEFAULT_PHASE30_ROLES
        )
        rounds.append(Phase30MeetingRound(round_name=round_name, messages=messages))
    return tuple(rounds)


def _project_meeting(
    *,
    adapter: MeetingThreadProjectionAdapter,
    meeting_run_id: str,
    trigger: Phase30Trigger,
    rounds: tuple[Phase30MeetingRound, ...],
    final_report: str,
) -> Phase30ProjectionResult:
    try:
        thread_id = adapter.create_thread(
            meeting_run_id=meeting_run_id, trigger=trigger
        )
        message_ids: list[str] = []
        for round_data in rounds:
            for message in round_data.messages:
                message_ids.append(
                    adapter.post_message(
                        thread_id=thread_id,
                        role=message.role,
                        content=f"[{round_data.round_name}] {message.content}",
                    )
                )
        message_ids.append(
            adapter.post_message(
                thread_id=thread_id,
                role="ceo_coordinator",
                content=final_report,
            )
        )
        return Phase30ProjectionResult(
            thread_status="created",
            thread_id=thread_id,
            message_ids=tuple(message_ids),
        )
    except Exception:
        return Phase30ProjectionResult(
            thread_status="failed",
            thread_id="",
            message_ids=(),
            error="projection_failed",
        )


def _build_final_report(
    *,
    meeting_run_id: str,
    trigger: Phase30Trigger,
    rounds: tuple[Phase30MeetingRound, ...],
    consensus: Phase30ConsensusPacket,
    validation_result: MeetingValidationResult | None = None,
    audit_result: MeetingAuditResult | None = None,
) -> str:
    role_lines = []
    for round_data in rounds:
        role_lines.append(f"## {round_data.round_name.title()} Round")
        for message in round_data.messages:
            role_lines.append(
                f"- **{message.role}**: {_sanitize_text(message.content)}"
            )
    return "\n".join(
        [
            f"# Phase 30 Meeting Final Report — {meeting_run_id}",
            "",
            f"- Trigger: {_sanitize_text(trigger.trigger_text)}",
            f"- Consensus reached: {str(consensus.consensus_reached).lower()}",
            f"- Escalation required: {str(consensus.escalation_required).lower()}",
            f"- Summary: {_sanitize_text(consensus.summary)}",
            *_validation_report_lines(validation_result),
            *_audit_report_lines(audit_result),
            "",
            *role_lines,
            "",
        ]
    )


def _validation_report_lines(
    validation_result: MeetingValidationResult | None,
) -> list[str]:
    if validation_result is None:
        return []
    return [
        f"- Validation provider: {_sanitize_text(validation_result.provider)}",
        f"- Validation model: {_sanitize_text(validation_result.model)}",
        f"- Validation verdict: {_sanitize_text(validation_result.verdict)}",
        f"- Validation summary: {_sanitize_text(validation_result.summary)}",
    ]


def _audit_report_lines(audit_result: MeetingAuditResult | None) -> list[str]:
    if audit_result is None:
        return []
    return [
        f"- Audit provider: {_sanitize_text(audit_result.provider)}",
        f"- Audit model: {_sanitize_text(audit_result.model)}",
        f"- Audit verdict: {_sanitize_text(audit_result.verdict)}",
        f"- Audit summary: {_sanitize_text(audit_result.summary)}",
    ]


def _run_validation(
    *,
    validation_runner: MeetingValidationRunner | None,
    validation_packet: Phase30ValidationPacket,
) -> MeetingValidationResult | None:
    if validation_runner is None:
        return None
    try:
        return validation_runner(validation_packet)
    except Exception:
        return MeetingValidationResult(
            status="failed",
            provider="validation-runner",
            model="unknown",
            verdict="block",
            confidence=0.0,
            summary="validation runner failed",
            blockers=("validation runner failed",),
        )


def _run_audit(
    *,
    audit_runner: MeetingAuditRunner | None,
    validation_packet: Phase30ValidationPacket,
    validation_result: MeetingValidationResult | None,
) -> MeetingAuditResult | None:
    if audit_runner is None:
        return None
    should_audit = validation_packet.consensus.escalation_required or (
        validation_result is not None and validation_result.blocks_release
    )
    if not should_audit:
        return None
    try:
        return audit_runner(
            validation_packet,
            validation_result=validation_result,
        )
    except Exception:
        return MeetingAuditResult(
            status="failed",
            provider="audit-runner",
            model="unknown",
            verdict="requires_changes",
            summary="audit runner failed",
            findings=("audit runner failed",),
        )


def _role_outputs_payload(
    rounds: tuple[Phase30MeetingRound, ...],
    *,
    opencode_results: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    outputs: dict[str, dict[str, str]] = {role: {} for role in DEFAULT_PHASE30_ROLES}
    for round_data in rounds:
        for message in round_data.messages:
            outputs.setdefault(message.role, {})[round_data.round_name] = (
                message.content
            )
    results = opencode_results or []
    return {
        "role_outputs": outputs,
        "opencode_used": bool(results),
        "opencode_results": results,
    }


def _opencode_results(provider: RoleOutputProvider) -> list[dict[str, object]]:
    if isinstance(provider, OpenCodeGoRoleOutputProvider):
        return [result.to_dict() for result in provider.last_results]
    return []


def _build_opencode_role_prompt(
    *, role: str, round_name: str, trigger: Phase30Trigger
) -> str:
    return "\n".join(
        [
            "You are one role in the AI_Agent virtual company meeting system.",
            f"Role: {role}",
            f"Round: {round_name}",
            f"Meeting trigger: {_sanitize_text(trigger.trigger_text)}",
            "Respond in Korean with the role's concrete meeting contribution.",
            "If the request is unsafe or evidence is insufficient, "
            "start with BLOCKER:.",
        ]
    )


def _write_opencode_context_file(prompt: str) -> Path:
    fd, tmp_name = tempfile.mkstemp(
        prefix="phase31-opencode-context-", suffix=".md", text=True
    )
    with open(fd, "w", encoding="utf-8", closefd=True) as handle:
        handle.write(prompt)
        handle.write("\n")
    return Path(tmp_name)


def _default_opencode_go_runner(
    argv: list[str],
    *,
    input_text: str,
    timeout_sec: int,
    env: dict[str, str],
) -> dict[str, object]:
    started = time.monotonic()
    try:
        del input_text
        completed = subprocess.run(
            argv,
            check=False,
            text=True,
            capture_output=True,
            timeout=timeout_sec,
            env=env,
        )
        return {
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "duration_sec": time.monotonic() - started,
            "timed_out": False,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "returncode": 124,
            "stdout": exc.stdout or "",
            "stderr": "opencode-go timed out",
            "duration_sec": time.monotonic() - started,
            "timed_out": True,
        }
    except OSError:
        return {
            "returncode": 127,
            "stdout": "",
            "stderr": "opencode-go executable unavailable",
            "duration_sec": time.monotonic() - started,
            "timed_out": False,
        }


def _extract_opencode_content(stdout: str) -> str:
    parsed = _try_json(stdout)
    if isinstance(parsed, dict):
        content = _first_text_field(parsed)
        if content:
            return _sanitize_text(content)
    if isinstance(parsed, list):
        for item in reversed(parsed):
            if isinstance(item, dict):
                content = _first_text_field(item)
                if content:
                    return _sanitize_text(content)
    for line in reversed(stdout.splitlines()):
        parsed_line = _try_json(line)
        if isinstance(parsed_line, dict):
            content = _first_text_field(parsed_line)
            if content:
                return _sanitize_text(content)
    cleaned = _sanitize_text(stdout)
    return cleaned or "BLOCKER: opencode-go role output was empty"


def _first_text_field(payload: dict[str, object]) -> str:
    for key in ("content", "text", "message", "output"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    part = payload.get("part")
    if isinstance(part, dict):
        value = part.get("text")
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _try_json(text: str) -> object:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _coerce_float(value: object, default: float) -> float:
    if isinstance(value, int | float | str):
        try:
            return float(value)
        except ValueError:
            return default
    return default


def _coerce_int(value: object, default: int) -> int:
    if isinstance(value, int | float | str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _extract_marker(role: str, content: str, marker: str) -> str:
    index = content.upper().find(marker)
    if index < 0:
        return f"{role}: {_sanitize_text(content)}"
    return f"{role}: {_sanitize_text(content[index + len(marker) :].strip())}"


def _new_meeting_run_id() -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")
    return f"phase30_meeting_e2e_{stamp}"


def _sanitize_text(text: str) -> str:
    sanitized = _BEARER_RE.sub("bearer [redacted]", text)
    sanitized = _SECRET_RE.sub(
        lambda match: match.group(0).split("=", 1)[0] + "=[redacted]", sanitized
    )
    sanitized = sanitized.replace("@everyone", "@\u000beveryone")
    sanitized = sanitized.replace("@here", "@\u000bhere")
    return sanitized.strip()


def _write_json(path: Path, payload: dict[str, object]) -> None:
    _write_text(
        path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent), text=True
    )
    tmp_path = Path(tmp_name)
    try:
        with open(fd, "w", encoding="utf-8", closefd=True) as handle:
            handle.write(text)
            handle.flush()
        tmp_path.replace(path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


__all__ = [
    "DEFAULT_PHASE30_ROLES",
    "DiscordHttpClient",
    "DiscordRestProjectionAdapter",
    "FakeMeetingThreadProjectionAdapter",
    "InjectedRoleOutputProvider",
    "MeetingAuditResult",
    "MeetingAuditRunner",
    "MeetingValidationResult",
    "MeetingValidationRunner",
    "OpenCodeGoCallResult",
    "OpenCodeGoRoleOutputProvider",
    "OpenCodeGoRunner",
    "Phase30ConsensusPacket",
    "Phase30MeetingE2EResult",
    "Phase30MeetingRound",
    "Phase30ProjectionResult",
    "Phase30RoleMessage",
    "Phase30Session",
    "Phase30Trigger",
    "Phase30ValidationPacket",
    "derive_consensus_packet",
    "inspect_phase30_meeting",
    "normalize_phase30_gateway_input",
    "run_phase30_meeting_e2e",
]
