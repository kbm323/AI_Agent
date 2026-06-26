"""Phase 28 full live closed-loop controlled pilot.

This module verifies the Hermes-first closed-loop path:
Hermes Gateway input -> MeetingRun -> routing/scheduling -> workers ->
validation -> Gate 9 projection safety -> controlled projection publish.

It is a controlled smoke layer. By default it uses deterministic/fake
boundaries and performs no live Discord call and no live worker CLI call. If
controlled live projection is explicitly requested, a fake/injected HTTP
callable is required; otherwise the run fails closed before any live boundary.
"""

from __future__ import annotations

import json
import re
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum, unique
from pathlib import Path
from typing import Any

from .command_surface import (
    CommandSurfaceMode,
    HermesGatewayCommandSurfacePolicy,
)
from .orchestrator import RuntimeOrchestrator
from .projection import (
    DiscordLiveBoundaryPolicy,
    FakeDiscordProjectionSink,
    LiveDiscordProjectionSink,
    _sanitize_discord_content,
)
from .schemas import DiscordProjectionEvent
from .service_supervision import ServiceSupervisionPolicy
from .worker_boundary_smoke import (
    BoundarySmokeStatus,
    LiveWorkerBoundarySmokePolicy,
)

PHASE28_PILOT_ID = "phase28_full_live_closed_loop_pilot"
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")

_PHASE28_STAGE_SEQUENCE = (
    "gateway_input_received",
    "policy_verified",
    "meeting_run_created",
    "meeting_run_routed",
    "meeting_run_scheduled",
    "workers_completed",
    "validation_completed",
    "projection_safety_verified",
    "projection_published",
    "artifact_written",
)


@unique
class ClosedLoopStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"


@dataclass(frozen=True)
class GatewayInput:
    """Synthetic Hermes Gateway input for the controlled closed-loop pilot."""

    trigger_text: str = (
        "AI virtual entertainment company Phase 28 controlled closed-loop smoke"
    )
    user_id: str = "phase28-user"
    channel_id: str = "phase28-channel"
    guild_id: str = "1505600166676271244"
    thread_id: str = ""
    trace_id: str = "trace-phase28-controlled-smoke"
    surface: CommandSurfaceMode = CommandSurfaceMode.HERMES_EXISTING_GATEWAY
    priority: str = "P1"

    def to_dict(self) -> dict[str, object]:
        return {
            "trigger_text": self.trigger_text,
            "user_id": self.user_id,
            "channel_id": self.channel_id,
            "guild_id": self.guild_id,
            "thread_id": self.thread_id,
            "trace_id": self.trace_id,
            "surface": self.surface.value,
            "priority": self.priority,
        }


@dataclass(frozen=True)
class ClosedLoopDecision:
    """Policy or safety decision for the Phase 28 loop."""

    status: ClosedLoopStatus
    reason: str = ""
    safe_content: str = ""


@dataclass(frozen=True)
class ProjectionSafetyPolicy:
    """Gate 9 projection safety verifier.

    The policy returns sanitized, user-facing content and fails closed when the
    projection cannot preserve traceability or user-visible safety.
    """

    max_content_length: int = 1900
    allowed_mentions_constrained: bool = True
    break_mass_mentions: bool = True
    omit_raw_worker_outputs: bool = True
    preserve_trace_id: bool = True
    redact_secret_like_values: bool = True

    @classmethod
    def current_verified(
        cls,
        *,
        max_content_length: int = 1900,
    ) -> ProjectionSafetyPolicy:
        return cls(max_content_length=max_content_length)

    def evaluate(
        self,
        *,
        content: str,
        trace_id: str,
        raw_worker_outputs: tuple[str, ...],
    ) -> ClosedLoopDecision:
        if self.max_content_length <= 0:
            return ClosedLoopDecision(
                ClosedLoopStatus.FAIL, "content_length_cap_invalid"
            )
        if not self.allowed_mentions_constrained:
            return ClosedLoopDecision(
                ClosedLoopStatus.FAIL, "allowed_mentions_not_constrained"
            )
        if not self.break_mass_mentions:
            return ClosedLoopDecision(
                ClosedLoopStatus.FAIL, "mass_mentions_not_broken"
            )
        if not self.omit_raw_worker_outputs:
            return ClosedLoopDecision(
                ClosedLoopStatus.FAIL, "raw_worker_outputs_not_omitted"
            )
        if not self.preserve_trace_id or not trace_id:
            return ClosedLoopDecision(
                ClosedLoopStatus.FAIL, "trace_id_required"
            )
        if not self.redact_secret_like_values:
            return ClosedLoopDecision(
                ClosedLoopStatus.FAIL, "secret_redaction_required"
            )

        base = content.strip()
        if not base:
            return ClosedLoopDecision(
                ClosedLoopStatus.FAIL, "projection_content_required"
            )

        # Raw worker outputs are deliberately not copied into the projection;
        # the loop records that they were omitted.
        del raw_worker_outputs
        with_trace = f"trace_id={trace_id}\n{base}\n- raw_worker_outputs: omitted"
        safe = _sanitize_discord_content(with_trace)
        if trace_id not in safe:
            return ClosedLoopDecision(
                ClosedLoopStatus.FAIL, "trace_id_not_preserved"
            )
        if len(safe) > self.max_content_length:
            safe = safe[: self.max_content_length]
            if trace_id not in safe:
                return ClosedLoopDecision(
                    ClosedLoopStatus.FAIL,
                    "trace_id_not_preserved_after_content_cap",
                )
        if not safe.strip():
            return ClosedLoopDecision(
                ClosedLoopStatus.FAIL, "projection_content_required"
            )
        return ClosedLoopDecision(
            status=ClosedLoopStatus.PASS,
            reason="",
            safe_content=safe,
        )


@dataclass(frozen=True)
class ClosedLoopPilotPolicy:
    """Phase 28 guardrail composition across Phases 24-27."""

    command_surface_policy: HermesGatewayCommandSurfacePolicy
    discord_boundary_policy: DiscordLiveBoundaryPolicy
    worker_boundary_policy: LiveWorkerBoundarySmokePolicy
    service_supervision_policy: ServiceSupervisionPolicy
    projection_safety_policy: ProjectionSafetyPolicy

    @classmethod
    def current_verified(
        cls,
        *,
        command_surface_policy: HermesGatewayCommandSurfacePolicy | None = None,
        discord_boundary_policy: DiscordLiveBoundaryPolicy | None = None,
        worker_boundary_policy: LiveWorkerBoundarySmokePolicy | None = None,
        service_supervision_policy: ServiceSupervisionPolicy | None = None,
        projection_safety_policy: ProjectionSafetyPolicy | None = None,
    ) -> ClosedLoopPilotPolicy:
        return cls(
            command_surface_policy=(
                command_surface_policy
                or HermesGatewayCommandSurfacePolicy.current_verified()
            ),
            discord_boundary_policy=(
                discord_boundary_policy or DiscordLiveBoundaryPolicy.current_verified()
            ),
            worker_boundary_policy=(
                worker_boundary_policy
                or LiveWorkerBoundarySmokePolicy.current_verified()
            ),
            service_supervision_policy=(
                service_supervision_policy
                or ServiceSupervisionPolicy.current_verified()
            ),
            projection_safety_policy=(
                projection_safety_policy or ProjectionSafetyPolicy.current_verified()
            ),
        )

    def evaluate(
        self,
        *,
        gateway_input: GatewayInput | None = None,
    ) -> ClosedLoopDecision:
        gateway_input = gateway_input or GatewayInput()
        command = self.command_surface_policy.evaluate(
            requested_surface=gateway_input.surface,
            require_mention=self.discord_boundary_policy.require_mention,
            thread_require_mention=(
                self.discord_boundary_policy.thread_require_mention
            ),
            free_response_channels=(
                self.discord_boundary_policy.free_response_channels
            ),
        )
        if not command.allowed:
            return ClosedLoopDecision(
                ClosedLoopStatus.FAIL, f"command_surface:{command.reason}"
            )

        worker = self.worker_boundary_policy.evaluate(
            uses_packet_input=_check_passed(
                self.worker_boundary_policy, "packet_based_input"
            ),
            model_provider_recorded=_check_passed(
                self.worker_boundary_policy, "model_provider_recorded"
            ),
            timeout_fail_closed=_check_passed(
                self.worker_boundary_policy, "timeout_fail_closed"
            ),
            nonzero_exit_fail_closed=_check_passed(
                self.worker_boundary_policy, "nonzero_exit_fail_closed"
            ),
            output_sanitized=_check_passed(
                self.worker_boundary_policy, "output_sanitized"
            ),
            quota_gate_checked=_check_passed(
                self.worker_boundary_policy, "quota_gate_checked"
            ),
            no_shell_true=_check_passed(
                self.worker_boundary_policy, "no_shell_true"
            ),
            no_direct_env_passthrough=_check_passed(
                self.worker_boundary_policy,
                "no_direct_env_passthrough",
            ),
        )
        if worker.status is not BoundarySmokeStatus.PASS:
            failed = ",".join(check.name for check in worker.failed_checks)
            return ClosedLoopDecision(
                ClosedLoopStatus.FAIL, f"worker_boundary:{failed}"
            )

        service = self.service_supervision_policy.evaluate()
        if service.status.value != ClosedLoopStatus.PASS.value:
            return ClosedLoopDecision(
                ClosedLoopStatus.FAIL,
                f"service_supervision:{service.reason}",
            )

        return ClosedLoopDecision(ClosedLoopStatus.PASS, "")


@dataclass(frozen=True)
class ClosedLoopPilotResult:
    """Structured Phase 28 pilot result."""

    ok: bool
    status: ClosedLoopStatus
    mode: str
    trace_id: str
    meeting_run_id: str
    final_state: str
    projection_status: str
    projection_content: str
    stage_sequence: tuple[str, ...]
    artifact_path: str
    live_discord_attempted: bool = False
    discord_message_id: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "status": self.status.value,
            "mode": self.mode,
            "trace_id": self.trace_id,
            "meeting_run_id": self.meeting_run_id,
            "final_state": self.final_state,
            "projection_status": self.projection_status,
            "projection_content": self.projection_content,
            "stage_sequence": list(self.stage_sequence),
            "artifact_path": self.artifact_path,
            "live_discord_attempted": self.live_discord_attempted,
            "discord_message_id": self.discord_message_id,
            "error": self.error,
        }


def run_phase28_closed_loop_pilot(
    *,
    root: str | Path,
    gateway_input: GatewayInput | None = None,
    policy: ClosedLoopPilotPolicy | None = None,
    raw_worker_outputs: tuple[str, ...] = (),
    controlled_live_projection: bool = False,
    env: Mapping[str, str] | None = None,
    discord_http_post: Callable[..., Mapping[str, Any]] | None = None,
    target_profile: str = "aicompanyassistant",
    target_channel_id: str = "phase28-channel",
) -> ClosedLoopPilotResult:
    """Run the Phase 28 controlled closed-loop pilot."""

    root = Path(root)
    gateway_input = gateway_input or GatewayInput()
    policy = policy or ClosedLoopPilotPolicy.current_verified()
    mode = (
        "controlled-live-projection"
        if controlled_live_projection
        else "controlled-dry-run"
    )

    if not _SAFE_ID_RE.fullmatch(gateway_input.trace_id):
        safe_trace = _sanitize_discord_content(gateway_input.trace_id).replace(
            "[redacted]", "REDACTED"
        )
        return _failed_result(
            root=root,
            mode=mode,
            trace_id="invalid_trace_id",
            meeting_run_id="",
            error=f"invalid_trace_id:{safe_trace}",
        )

    if controlled_live_projection and discord_http_post is None:
        return _failed_result(
            root=root,
            mode=mode,
            trace_id=gateway_input.trace_id,
            meeting_run_id="",
            error="live_projection_requires_injected_http",
        )

    policy_decision = policy.evaluate(gateway_input=gateway_input)
    if policy_decision.status is ClosedLoopStatus.FAIL:
        return _failed_result(
            root=root,
            mode=mode,
            trace_id=gateway_input.trace_id,
            meeting_run_id="",
            error=policy_decision.reason,
        )

    meeting_run_id = f"mr-phase28-{gateway_input.trace_id}"
    orchestrator = RuntimeOrchestrator(root=root)
    try:
        orchestrated = orchestrator.run(
            meeting_run_id=meeting_run_id,
            trigger_text=gateway_input.trigger_text,
            user_id=gateway_input.user_id,
            channel_id=gateway_input.channel_id,
            thread_id=gateway_input.thread_id,
            guild_id=gateway_input.guild_id,
            hermes_session_id=gateway_input.trace_id,
            priority=gateway_input.priority,
            simulation=True,
        )
    except Exception:
        return _failed_result(
            root=root,
            mode=mode,
            trace_id=gateway_input.trace_id,
            meeting_run_id=meeting_run_id,
            error="orchestrator_failed",
        )

    projection_decision = policy.projection_safety_policy.evaluate(
        content=orchestrated.projection_event.content,
        trace_id=gateway_input.trace_id,
        raw_worker_outputs=raw_worker_outputs,
    )
    if projection_decision.status is ClosedLoopStatus.FAIL:
        return _failed_result(
            root=root,
            mode=mode,
            trace_id=gateway_input.trace_id,
            meeting_run_id=meeting_run_id,
            final_state=str(orchestrated.meeting_run.state.value),
            error=f"projection_safety:{projection_decision.reason}",
        )

    event = DiscordProjectionEvent(
        event_id=f"proj_{meeting_run_id}_phase28_controlled",
        meeting_run_id=meeting_run_id,
        bot_role="ceo_coordinator",
        target_channel_id=target_channel_id,
        target_thread_id="",
        content=projection_decision.safe_content,
        source="phase28_closed_loop_pilot",
        source_id=gateway_input.trace_id,
    )

    if controlled_live_projection:
        sink = LiveDiscordProjectionSink(
            env={} if env is None else env,
            http_post=discord_http_post,
            boundary_policy=policy.discord_boundary_policy,
            profile=target_profile,
            guild_id=gateway_input.guild_id,
        )
        publish = sink.publish(event)
        live_attempted = publish.status != "blocked"
    else:
        publish = FakeDiscordProjectionSink().publish(event)
        live_attempted = False

    status = ClosedLoopStatus.PASS
    error = ""
    ok = True
    if publish.status != "published":
        status = ClosedLoopStatus.FAIL
        error = f"live_projection_failed:{publish.error or publish.status}"
        ok = False

    result = ClosedLoopPilotResult(
        ok=ok,
        status=status,
        mode=mode,
        trace_id=gateway_input.trace_id,
        meeting_run_id=meeting_run_id,
        final_state=str(orchestrated.meeting_run.state.value),
        projection_status=publish.status,
        projection_content=projection_decision.safe_content,
        stage_sequence=_PHASE28_STAGE_SEQUENCE,
        artifact_path=_artifact_path_string(),
        live_discord_attempted=live_attempted,
        discord_message_id=publish.discord_message_id,
        error=error,
    )
    _write_artifact(root, result)
    return result


def _check_passed(policy: LiveWorkerBoundarySmokePolicy, name: str) -> bool:
    for check in policy.checks:
        if check.name == name:
            return check.passed
    return False


def _failed_result(
    *,
    root: Path,
    mode: str,
    trace_id: str,
    meeting_run_id: str,
    error: str,
    final_state: str = "",
) -> ClosedLoopPilotResult:
    result = ClosedLoopPilotResult(
        ok=False,
        status=ClosedLoopStatus.FAIL,
        mode=mode,
        trace_id=trace_id,
        meeting_run_id=meeting_run_id,
        final_state=final_state,
        projection_status="not_attempted",
        projection_content="",
        stage_sequence=("gateway_input_received", "failed_closed"),
        artifact_path=_artifact_path_string(),
        live_discord_attempted=False,
        error=error,
    )
    _write_artifact(root, result)
    return result


def _artifact_path_string() -> str:
    return "runtime/phase28-closed-loop/phase28_closed_loop_report.json"


def _write_artifact(root: Path, result: ClosedLoopPilotResult) -> Path:
    path = root / _artifact_path_string()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=".phase28_closed_loop_report.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with open(fd, "w", encoding="utf-8", closefd=True) as handle:
            json.dump(result.to_dict(), handle, ensure_ascii=False, indent=2)
            handle.flush()
        tmp_path.replace(path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
    return path


__all__ = [
    "ClosedLoopDecision",
    "ClosedLoopPilotPolicy",
    "ClosedLoopPilotResult",
    "ClosedLoopStatus",
    "GatewayInput",
    "ProjectionSafetyPolicy",
    "run_phase28_closed_loop_pilot",
]
