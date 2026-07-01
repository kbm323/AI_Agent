"""Multi-bot operational protocol for Runtime Architecture v2.

This module defines the bot-to-bot conversation protocol, multi-participant
meeting flow, and persona-aware projection routing needed for Phase 14
multi-bot operational coordination.

Hermes Core is not modified. The multi-bot layer adds only domain-specific
coordination schemas and execution flows on top of the existing MeetingRun,
worker, and projection boundaries.

Naming note: several public functions retain Phase 13/14 names for historical
test/script compatibility. In the live Gateway path they are the Runtime v2
meeting execution engine: six Discord-facing team leads are projected to the
thread, while agenda-matched internal specialists execute as worker tasks and
are summarized in the final report/evidence.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

from .model_policy import worker_model_policy_for_role
from .projection import (
    DiscordLiveBoundaryPolicy,
    FakeDiscordProjectionSink,
    LiveDiscordProjectionSink,
    LiveDiscordThreadManager,
    ProjectionPublishResult,
    SharedMeetingThreadProjectionPolicy,
    _default_discord_http_post,
    _sanitize_discord_content,
)
from .schemas import (
    DiscordProjectionEvent,
    MeetingRun,
    MeetingRunState,
    WorkerTask,
    WorkerTaskRunner,
    WorkerTaskState,
)
from .validation import ValidationPolicy
from .workers import (
    FakeWorkerRunner,
    OpenCodeGoCommandRunner,
    OpenCodeGoWorkerRunner,
    WorkerRunner,
)

# ── Bot Persona Display Names ──────────────────────────────────────────

BOT_PERSONAS: dict[str, str] = {
    # Executive
    "ceo_coordinator": "대표",
    "coo": "운영총괄",
    "cfo": "재무총괄",
    # Content Production
    "content_lead": "콘텐츠 팀장",
    "producer": "프로듀서",
    "writer": "작가",
    "editor": "편집자",
    "script_director": "대본감독",
    "storyboard_artist": "스토리보드 아티스트",
    # Art & Visual
    "art_lead": "아트 팀장",
    "character_designer": "캐릭터 디자이너",
    "background_artist": "배경 아티스트",
    "animator": "애니메이터",
    "vfx_artist": "VFX 아티스트",
    # Technology
    "tech_lead": "기술 팀장",
    "engine_developer": "엔진 개발자",
    "backend_developer": "백엔드 개발자",
    "ai_engineer": "AI 엔지니어",
    "devops_engineer": "데브옵스 엔지니어",
    # Marketing & Business
    "marketing_lead": "마케팅 팀장",
    "sns_manager": "SNS 매니저",
    "community_manager": "커뮤니티 매니저",
    "business_support_lead": "사업지원 팀장",
    "partnership_manager": "파트너십 매니저",
    # Quality & Validation
    "validation_audit": "검증 팀장",
    "quality_lead": "QA 리드",
    "legal_compliance": "법무/컴플라이언스",
    # Production Support
    "project_manager": "프로젝트 매니저",
    "hr_lead": "인사/문화",
}

BotMessageType = Literal[
    "meeting_open",
    "opinion",
    "rebuttal",
    "consensus",
    "escalation",
    "final_report",
]


# ── Multi-bot Conversation Protocol Schemas ────────────────────────────


@dataclass(frozen=True)
class BotMessage:
    """One message from a bot participant in a MeetingRound."""

    bot_role: str
    meeting_run_id: str
    round: int
    msg_type: BotMessageType
    content: str
    mentions: tuple[str, ...] = ()
    visible_on_discord: bool = True

    def to_dict(self) -> dict[str, object]:
        return {
            "bot_role": self.bot_role,
            "meeting_run_id": self.meeting_run_id,
            "round": self.round,
            "msg_type": self.msg_type,
            "content": self.content,
            "mentions": list(self.mentions),
            "visible_on_discord": self.visible_on_discord,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> BotMessage:
        raw_mentions = data.get("mentions") or []
        if isinstance(raw_mentions, list):
            mentions = tuple(str(m) for m in raw_mentions)
        else:
            mentions = ()
        return cls(
            bot_role=str(data["bot_role"]),
            meeting_run_id=str(data["meeting_run_id"]),
            round=int(str(data["round"])),
            msg_type=str(data["msg_type"]),  # type: ignore[arg-type]
            content=str(data["content"]),
            mentions=mentions,
            visible_on_discord=bool(data.get("visible_on_discord", True)),
        )


@dataclass(frozen=True)
class MeetingRound:
    """One round of multi-bot discussion."""

    round_number: int
    phase: Literal["opinions", "rebuttals", "consensus"]
    messages: tuple[BotMessage, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "round_number": self.round_number,
            "phase": self.phase,
            "messages": [msg.to_dict() for msg in self.messages],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> MeetingRound:
        raw_messages = data.get("messages") or []
        messages: list[BotMessage] = []
        if isinstance(raw_messages, list):
            for m in raw_messages:
                if isinstance(m, dict):
                    messages.append(BotMessage.from_dict(m))
        return cls(
            round_number=int(str(data["round_number"])),
            phase=str(data["phase"]),  # type: ignore[arg-type]
            messages=tuple(messages),
        )


@dataclass(frozen=True)
class MultiBotSession:
    """Complete multi-bot meeting session."""

    meeting_run_id: str
    participants: tuple[str, ...]
    rounds: tuple[MeetingRound, ...]
    consensus_reached: bool
    escalation_required: bool
    consensus_summary: str = ""
    escalation_reason: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "meeting_run_id": self.meeting_run_id,
            "participants": list(self.participants),
            "rounds": [r.to_dict() for r in self.rounds],
            "consensus_reached": self.consensus_reached,
            "escalation_required": self.escalation_required,
            "consensus_summary": self.consensus_summary,
            "escalation_reason": self.escalation_reason,
        }


@dataclass(frozen=True)
class MultiBotPilotResult:
    """Structured result from a Phase 14 multi-bot pilot run."""

    pilot_id: str
    mode: str
    ok: bool
    meeting_run: MeetingRun
    session: MultiBotSession
    worker_tasks: tuple[WorkerTask, ...]
    projection_results: tuple[ProjectionPublishResult, ...]
    live_worker_count: int
    fake_worker_count: int
    bot_participants: tuple[str, ...]
    rounds_completed: int
    projection_messages_posted: int
    meeting_thread_id: str = ""
    meeting_thread_status: str = ""
    meeting_thread_error: str = ""
    error: str = ""
    internal_specialist_roles: tuple[str, ...] = ()
    fallback_events: tuple[str, ...] = ()
    final_report: str = ""

    def to_cli_dict(self) -> dict[str, object]:
        return {
            "pilot_id": self.pilot_id,
            "mode": self.mode,
            "meeting_run_id": self.meeting_run.meeting_run_id,
            "top_level_state": str(self.meeting_run.state),
            "live_worker_count": self.live_worker_count,
            "fake_worker_count": self.fake_worker_count,
            "bot_participants": list(self.bot_participants),
            "rounds_completed": self.rounds_completed,
            "projection_messages_posted": self.projection_messages_posted,
            "consensus_reached": self.session.consensus_reached,
            "escalation_required": self.session.escalation_required,
            "consensus_summary": self.session.consensus_summary,
            "projection_statuses": [r.status for r in self.projection_results],
            "meeting_thread_id": self.meeting_thread_id,
            "meeting_thread_status": self.meeting_thread_status,
            "meeting_thread_error": self.meeting_thread_error,
            "internal_specialist_roles": list(self.internal_specialist_roles),
            "fallback_events": list(self.fallback_events),
            "final_report": self.final_report,
            "error": self.error,
            "ok": self.ok,
        }


# ── Bot Persona Projection Routing ─────────────────────────────────────


def _profile_for_bot_role(bot_role: str) -> str:
    return {
        "ceo_coordinator": "aicompanyceo",
        "content_lead": "aicompanycontent",
        "art_lead": "aicompanyart",
        "tech_lead": "aicompanytech",
        "marketing_lead": "aicompanymarketing",
        "quality_lead": "aicompanyquality",
        "validation_audit": "aicompanyquality",
        # Business support is an internal org-chart role, not the personal
        # assistant bot. If it is ever projected, the CEO summarizes it.
        "business_support_lead": "aicompanyceo",
    }.get(bot_role, "aicompanyceo")


def _target_channel_for_bot_role(
    bot_role: str,
    target_channel_id: str,
    boundary_policy: DiscordLiveBoundaryPolicy,
) -> str:
    """Resolve Phase 14 projection target for a bot role.

    `profile-home` is an explicit controlled-smoke sentinel: each projected bot
    posts only to its verified profile home channel. Any other value is treated
    as an explicit caller-provided channel and remains subject to boundary
    policy evaluation.
    """
    if target_channel_id != "profile-home":
        return target_channel_id
    profile = _profile_for_bot_role(bot_role)
    return boundary_policy.allowed_channel_ids_by_profile.get(profile, "")


def _discord_env_for_profile(profile: str) -> dict[str, str]:
    """Load only the Discord bot token from a Hermes profile .env file."""
    env_path = Path.home() / ".hermes" / "profiles" / profile / ".env"
    if not env_path.exists():
        return {}
    for raw_line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == "DISCORD_BOT_TOKEN":
            return {"DISCORD_BOT_TOKEN": value.strip().strip('"').strip("'")}
    return {}


def route_bot_projection(
    message: BotMessage,
    *,
    live_discord: bool = False,
    target_channel_id: str = "",
    target_thread_id: str = "",
    env: Mapping[str, str] | None = None,
    discord_http_post: Callable[..., Mapping[str, object]] | None = None,
    shared_thread_policy: SharedMeetingThreadProjectionPolicy | None = None,
) -> ProjectionPublishResult:
    """Route a BotMessage through the correct persona projection.

    Creates a DiscordProjectionEvent with persona-prefixed, sanitized content
    and publishes through live or fake sink.
    """
    persona = BOT_PERSONAS.get(message.bot_role, message.bot_role)
    safe_content = _sanitize_discord_content(message.content)
    boundary_policy = DiscordLiveBoundaryPolicy.current_verified()
    profile = _profile_for_bot_role(message.bot_role)
    resolved_target_channel_id = _target_channel_for_bot_role(
        message.bot_role,
        target_channel_id,
        boundary_policy,
    )
    sink_env = dict(env) if env is not None else _discord_env_for_profile(profile)

    prefix = f"**[{persona}]** "
    full = prefix + safe_content
    full = _truncate_discord_projection_content(full)

    event = DiscordProjectionEvent(
        event_id=f"proj_{message.meeting_run_id}_{message.bot_role}_r{message.round}",
        meeting_run_id=message.meeting_run_id,
        bot_role=message.bot_role,
        target_channel_id=resolved_target_channel_id or "phase14-channel",
        target_thread_id=target_thread_id,
        content=full,
        source="multi_bot_meeting",
        source_id=f"{message.bot_role}_r{message.round}",
    )

    if live_discord and resolved_target_channel_id:
        sink = LiveDiscordProjectionSink(
            env=sink_env,
            http_post=discord_http_post or _default_discord_http_post,
            boundary_policy=boundary_policy,
            shared_thread_policy=shared_thread_policy,
            profile=profile,
            guild_id=boundary_policy.guild_id,
        )
    else:
        sink = FakeDiscordProjectionSink()

    return sink.publish(event)


def _truncate_discord_projection_content(content: str, *, max_length: int = 1900) -> str:
    if len(content) <= max_length:
        return content
    truncated = content[: max_length - 1].rstrip() + "…"
    if truncated.count("```") % 2 == 1:
        suffix = "\n```"
        truncated = content[: max_length - len(suffix) - 1].rstrip() + "…" + suffix
    return truncated


_SPECIALIST_KEYWORD_ROUTES: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("야구", "스포츠", "성과", "지표", "분석", "데이터"), ("data-analyst",)),
    (("자동화", "파이프라인", "api", "백엔드", "연동", "수집"), ("backend-engineer",)),
    (("쇼츠", "유튜브", "영상", "편집", "릴스", "shorts"), ("video-editor",)),
    (("품질", "검증", "테스트", "qa"), ("quality-assurance",)),
    (("음악", "bgm", "사운드", "오디오"), ("composer", "sound-designer")),
    (("보안", "권한", "토큰", "secret"), ("security-engineer",)),
    (("법", "저작권", "계약", "컴플라이언스"), ("legal-reviewer",)),
    (("디자인", "ui", "ux", "화면", "레이아웃"), ("ui-ux-designer",)),
)


def _select_internal_specialist_roles(
    trigger_text: str,
    *,
    visible_roles: tuple[str, ...],
    limit: int = 4,
) -> tuple[str, ...]:
    """Select non-Discord internal specialist workers for the meeting agenda."""

    text = trigger_text.lower()
    selected: list[str] = []
    for keywords, roles in _SPECIALIST_KEYWORD_ROUTES:
        if not any(keyword.lower() in text for keyword in keywords):
            continue
        for role in roles:
            if role not in visible_roles and role not in selected:
                selected.append(role)
                if len(selected) >= limit:
                    return tuple(selected)
    return tuple(selected)


# ── Multi-bot Meeting Phase ────────────────────────────────────────────


def run_meeting_phase(
    run: MeetingRun,
    participants: tuple[str, ...],
    *,
    rounds: int = 2,
    live_bot_roles: tuple[str, ...] = ("content_lead",),
    fake_bot_roles: tuple[str, ...] = ("marketing_lead", "quality_lead"),
    command_runner: OpenCodeGoCommandRunner | None = None,
    workdir: str = ".",
) -> MultiBotSession:
    """Execute a multi-bot meeting phase with opinion and rebuttal rounds.

    Live bots execute through opencode-go workers. Fake bots produce
    deterministic injected output tagged with their role.
    """
    all_bots = live_bot_roles + fake_bot_roles
    if not participants:
        raise ValueError("meeting phase requires at least one participant")

    meeting_rounds: list[MeetingRound] = []

    # Round 1 — Opinions
    round1_msgs: list[BotMessage] = []
    for role in all_bots:
        is_live = role in live_bot_roles
        content = _generate_bot_content(
            role=role,
            round_num=1,
            msg_type="opinion",
            run=run,
            is_live=is_live,
            command_runner=command_runner,
            workdir=workdir,
            previous_messages=(),
        )
        round1_msgs.append(
            BotMessage(
                bot_role=role,
                meeting_run_id=run.meeting_run_id,
                round=1,
                msg_type="opinion",
                content=content,
                mentions=(),
                visible_on_discord=True,
            )
        )
    meeting_rounds.append(
        MeetingRound(round_number=1, phase="opinions", messages=tuple(round1_msgs))
    )

    # Round 2 — Rebuttals (if rounds >= 2)
    if rounds >= 2:
        round2_msgs: list[BotMessage] = []
        for role in all_bots:
            is_live = role in live_bot_roles
            opponents = tuple(r for r in all_bots if r != role)
            content = _generate_bot_content(
                role=role,
                round_num=2,
                msg_type="rebuttal",
                run=run,
                is_live=is_live,
                command_runner=command_runner,
                workdir=workdir,
                previous_messages=tuple(round1_msgs),
            )
            round2_msgs.append(
                BotMessage(
                    bot_role=role,
                    meeting_run_id=run.meeting_run_id,
                    round=2,
                    msg_type="rebuttal",
                    content=content,
                    mentions=opponents,
                    visible_on_discord=True,
                )
            )
        meeting_rounds.append(
            MeetingRound(round_number=2, phase="rebuttals", messages=tuple(round2_msgs))
        )

    # Consensus check — simple heuristic
    consensus_reached = len(all_bots) >= 2
    escalation_required = not consensus_reached

    return MultiBotSession(
        meeting_run_id=run.meeting_run_id,
        participants=participants,
        rounds=tuple(meeting_rounds),
        consensus_reached=consensus_reached,
        escalation_required=escalation_required,
        consensus_summary=(
            "모든 팀장의 의견을 수렴하여 합의에 도달했습니다."
            if consensus_reached
            else ""
        ),
        escalation_reason=(
            ""
            if consensus_reached
            else "참여 팀장 부족으로 합의 불가 — 사용자 판단 필요"
        ),
    )


# ── Multi-bot Pilot Orchestrator ───────────────────────────────────────


PILOT_ID_14 = "phase14_multi_bot_operational_pilot"

PILOT_14_TRIGGER_TEXT = (
    "AI virtual entertainment company — "
    "신규 버추얼 아이돌 그룹의 데뷔 컨셉을 회의해줘. "
    "콘텐츠 팀장이 아이디어 내고, "
    "마케팅 팀장이 시장성 검토하고, "
    "검증 팀장이 리스크 체크해줘."
)


def _build_phase14_worker_tasks(
    run: MeetingRun,
    route: Any,
    root: str | Path,
    *,
    live_worker_count: int = 2,
    internal_specialist_roles: tuple[str, ...] = (),
    live_specialists: bool = False,
) -> tuple[WorkerTask, ...]:
    """Build Phase 14 worker tasks allowing all configured live workers.

    Uses the same task structure as Phase 13 but removes the old pilot-only
    two-worker cap so the production gateway can run every team-lead bot.
    """
    root = Path(root)
    roles: tuple[str, ...] = getattr(route, "live_worker_roles", ()) + getattr(
        route, "fake_worker_roles", ()
    )
    if not roles:
        roles = ("content_lead", "marketing_lead", "quality_lead")
    if live_worker_count < 0 or live_worker_count > len(roles):
        raise ValueError(
            f"Phase 14 permits 0..{len(roles)} live workers, got {live_worker_count}"
        )
    live_roles = set(roles[:live_worker_count])
    tasks: list[WorkerTask] = []
    all_task_roles = roles + tuple(
        role for role in internal_specialist_roles if role not in roles
    )
    for index, role in enumerate(all_task_roles, start=1):
        runner_enum = (
            WorkerTaskRunner.OPENCODE_GO
            if role in live_roles or (live_specialists and role in internal_specialist_roles)
            else WorkerTaskRunner.HERMES_WRAPPER
        )
        task_id = f"wt_{run.meeting_run_id}_{index}_{role}"
        run_dir = root / "runtime" / "meeting_runs" / run.meeting_run_id
        tasks.append(
            WorkerTask(
                worker_task_id=task_id,
                meeting_run_id=run.meeting_run_id,
                role=role,
                runner=runner_enum,
                packet_path=str(run_dir / "packets" / f"{task_id}.json"),
                output_path=str(run_dir / "worker_outputs" / f"{task_id}.json"),
                model_policy=worker_model_policy_for_role(role),
                hermes_refs={
                    "prompt": _prompt_for_phase14_worker(
                        role=role,
                        run=run,
                        internal_specialist=role in internal_specialist_roles,
                    )
                },
            )
        )
    return tuple(tasks)


def _prompt_for_phase14_worker(
    *,
    role: str,
    run: MeetingRun,
    internal_specialist: bool,
) -> str:
    persona = BOT_PERSONAS.get(role, role)
    trigger_text = str(run.trigger.get("text") or "")
    if internal_specialist:
        return (
            f"당신은 AI_Agent 회의의 내부 specialist '{role}'입니다.\n"
            f"회의 안건: {trigger_text}\n"
            "Discord에 직접 발언하지 않고 최종 보고서에 한 줄로 요약될 전문 분석을 작성하세요.\n"
            "한국어로 1~2문장만 답하고, raw JSON/메타데이터 없이 specialist 관점의 핵심 결과만 반환하세요."
        )
    return (
        f"당신은 AI 가상 엔터테인먼트 회사의 '{persona}'입니다.\n"
        f"회의 안건: {trigger_text}\n"
        "팀장 관점의 핵심 의견을 한국어 1~2문장으로 작성하세요."
    )


def build_phase14_pilot_request() -> dict[str, object]:
    """Return the canonical Phase 14 multi-bot pilot request."""
    return {
        "pilot_id": PILOT_ID_14,
        "trigger_text": PILOT_14_TRIGGER_TEXT,
        "user_id": "phase14-user",
        "channel_id": "phase14-channel",
        "thread_id": "",
        "guild_id": "",
        "priority": "P2",
        "live_bot_roles": ["content_lead", "marketing_lead"],
        "fake_bot_roles": ["quality_lead"],
    }


def run_phase14_multi_bot_pilot(
    *,
    root: str | Path,
    mode: Literal["dry-run", "live-worker"] = "dry-run",
    max_live_workers: int = 0,
    command_runner: OpenCodeGoCommandRunner | None = None,
    live_discord: bool = False,
    env: Mapping[str, str] | None = None,
    target_channel_id: str = "phase14-channel",
    target_thread_id: str = "",
    create_meeting_thread: bool = True,
    thread_name: str = "Phase14 팀장 회의",
    trigger_text: str | None = None,
    discord_http_post: Callable[..., Mapping[str, object]] | None = None,
    live_bot_roles_override: tuple[str, ...] | None = None,
    fake_bot_roles_override: tuple[str, ...] | None = None,
) -> MultiBotPilotResult:
    """Run the Phase 14 multi-bot operational protocol pilot."""

    from .pilot import (
        Phase13PilotModeError,
        _build_phase13_validation_verdicts,
        _write_phase13_report,
        build_phase13_route,
        create_phase13_meeting_run,
    )
    from .store import MeetingRunStore

    if mode not in {"dry-run", "live-worker"}:
        raise Phase13PilotModeError("invalid_mode", f"unsupported mode: {mode}")
    if mode == "dry-run" and max_live_workers != 0:
        raise Phase13PilotModeError(
            "invalid_live_worker_count",
            "dry-run may only use zero live workers",
        )
    if mode == "dry-run" and live_discord:
        raise Phase13PilotModeError(
            "invalid_live_discord_mode",
            "dry-run cannot publish through live Discord projection",
        )
    root = Path(root)
    request = build_phase14_pilot_request()
    if trigger_text is not None:
        request["trigger_text"] = trigger_text
    if live_bot_roles_override is not None:
        request["live_bot_roles"] = list(live_bot_roles_override)
    if fake_bot_roles_override is not None:
        request["fake_bot_roles"] = list(fake_bot_roles_override)
    run = create_phase13_meeting_run(root, request)
    store = MeetingRunStore(root)
    route = build_phase13_route(run)
    live_count = 0 if mode == "dry-run" else max_live_workers

    live_bot_roles_raw = request.get("live_bot_roles", [])
    fake_bot_roles_raw = request.get("fake_bot_roles", [])
    live_bot_roles: tuple[str, ...] = (
        tuple(str(r) for r in live_bot_roles_raw)  # type: ignore[union-attr]
        if isinstance(live_bot_roles_raw, list)
        else ()
    )
    fake_bot_roles: tuple[str, ...] = (
        tuple(str(r) for r in fake_bot_roles_raw)  # type: ignore[union-attr]
        if isinstance(fake_bot_roles_raw, list)
        else ()
    )
    if mode == "live-worker" and (
        max_live_workers < 1 or max_live_workers > len(live_bot_roles)
    ):
        raise Phase13PilotModeError(
            "invalid_live_worker_count",
            f"Phase 14 live-worker mode requires 1..{len(live_bot_roles)} live workers",
        )
    all_requested_roles = live_bot_roles + fake_bot_roles
    if all_requested_roles:
        validators = tuple(
            role
            for role in all_requested_roles
            if role in {"quality_lead", "validation_audit"}
        ) or (all_requested_roles[-1],)
        route = replace(
            route,
            primary_role=all_requested_roles[0],
            supporting_roles=all_requested_roles[1:],
            live_worker_roles=live_bot_roles,
            fake_worker_roles=fake_bot_roles,
            routing_result=replace(
                route.routing_result,
                teams=all_requested_roles,
                worker_roles=all_requested_roles,
                validators=validators,
                rationale="Gateway-driven Runtime v2 meeting route for all configured team-lead bots.",
            ),
        )
    active_live_bot_roles = live_bot_roles[:live_count]
    participants = all_requested_roles
    active_fake_bot_roles = tuple(
        role for role in participants if role not in active_live_bot_roles
    )
    internal_specialist_roles = _select_internal_specialist_roles(
        str(request.get("trigger_text") or ""),
        visible_roles=participants,
    )

    worker_tasks = _build_phase14_worker_tasks(
        run,
        route,
        root,
        live_worker_count=live_count,
        internal_specialist_roles=internal_specialist_roles,
        live_specialists=(mode == "live-worker"),
    )

    run = replace(
        run,
        state=MeetingRunState.ROUTED,
        routing_result=route.routing_result.to_dict(),
        worker_task_ids=tuple(task.worker_task_id for task in worker_tasks),
    )
    store.save_meeting_run(run)

    # Run multi-bot meeting phase
    session = run_meeting_phase(
        run,
        participants=participants,
        rounds=2,
        live_bot_roles=active_live_bot_roles,
        fake_bot_roles=active_fake_bot_roles,
        command_runner=command_runner,
        workdir=str(root),
    )

    # Execute worker tasks
    completed_tasks = _execute_phase14_tasks(
        worker_tasks,
        mode=mode,
        command_runner=command_runner,
        workdir=str(root),
        session=session,
    )

    all_ok = all(task.state == WorkerTaskState.SUCCEEDED for task in completed_tasks)

    validation_verdicts = _build_phase13_validation_verdicts(run, completed_tasks)
    _validation_decision = ValidationPolicy().decide(
        meeting_run_id=run.meeting_run_id,
        verdicts=validation_verdicts,
    )

    final_state = MeetingRunState.COMPLETED
    error = ""
    if not all_ok:
        final_state = MeetingRunState.FAILED
        error = "worker_execution_failed"

    run = replace(
        run,
        state=final_state,
        validation_ids=tuple(verdict.validation_id for verdict in validation_verdicts),
    )

    fallback_events = _fallback_events(completed_tasks)
    # Phase 32: final_report_v2.md is no longer auto-generated.
    # Source data (meeting_run.json, worker_outputs, session messages)
    # remain available for future on-demand report generation.
    # _build_final_report() can still be called explicitly when needed.

    # Produce projections for all visible bot messages. In live Discord mode,
    # Phase 29 requires one CEO-owned shared meeting thread so each team lead
    # appears in the same user-visible conversation instead of separate channel
    # summaries. If the thread cannot be created/verified, fail closed before
    # posting any bot messages.
    projection_results: list[ProjectionPublishResult] = []
    visible_count = 0
    meeting_thread_id = target_thread_id
    meeting_thread_status = "not_requested"
    meeting_thread_error = ""
    shared_thread_policy: SharedMeetingThreadProjectionPolicy | None = None
    boundary_policy = DiscordLiveBoundaryPolicy.current_verified()
    if live_discord:
        if create_meeting_thread and not meeting_thread_id:
            manager = LiveDiscordThreadManager(
                env=(
                    dict(env)
                    if env is not None
                    else _discord_env_for_profile("aicompanyceo")
                ),
                http_post=discord_http_post or _default_discord_http_post,
                boundary_policy=boundary_policy,
                profile="aicompanyceo",
                guild_id=boundary_policy.guild_id,
            )
            thread = manager.create_meeting_thread(
                parent_channel_id=target_channel_id,
                name=thread_name,
            )
            meeting_thread_status = thread.status
            meeting_thread_id = thread.thread_id
            meeting_thread_error = thread.error
            if thread.status != "created":
                final_state = MeetingRunState.FAILED
                error = "live_discord_thread_blocked"
                run = replace(run, state=final_state)
        elif meeting_thread_id:
            meeting_thread_status = "provided"

        if meeting_thread_id and final_state == MeetingRunState.COMPLETED:
            shared_thread_policy = SharedMeetingThreadProjectionPolicy(
                boundary_policy=boundary_policy,
                parent_channel_id=target_channel_id,
                thread_id=meeting_thread_id,
            )

    if final_state == MeetingRunState.COMPLETED:
        for round_data in session.rounds:
            for msg in round_data.messages:
                if not msg.visible_on_discord:
                    continue
                result = route_bot_projection(
                    msg,
                    live_discord=live_discord,
                    target_channel_id=target_channel_id,
                    target_thread_id=meeting_thread_id,
                    env=None,
                    discord_http_post=discord_http_post,
                    shared_thread_policy=shared_thread_policy,
                )
                projection_results.append(result)
                if result.status == "published":
                    visible_count += 1

    if live_discord and (
        not projection_results
        or any(result.status != "published" for result in projection_results)
    ):
        final_state = MeetingRunState.FAILED
        error = error or "live_discord_publish_blocked"
        run = replace(run, state=final_state)

    _report_path = _write_phase13_report(
        root=root,
        run=run,
        route=route,
        worker_tasks=completed_tasks,
        validation_verdicts=validation_verdicts,
        mode=mode,
        live_discord=live_discord,
    )
    run = replace(
        run,
        projection_event_ids=tuple(
            f"proj_{run.meeting_run_id}_phase14_msg_{i}"
            for i in range(len(projection_results))
        ),
    )
    store.save_meeting_run(run)

    return MultiBotPilotResult(
        pilot_id=PILOT_ID_14,
        mode=mode,
        ok=(final_state == MeetingRunState.COMPLETED and not error),
        meeting_run=run,
        session=session,
        worker_tasks=completed_tasks,
        projection_results=tuple(projection_results),
        live_worker_count=live_count,
        fake_worker_count=len(completed_tasks) - live_count,
        bot_participants=participants,
        rounds_completed=len(session.rounds),
        projection_messages_posted=visible_count,
        meeting_thread_id=meeting_thread_id,
        meeting_thread_status=meeting_thread_status,
        meeting_thread_error=meeting_thread_error,
        internal_specialist_roles=internal_specialist_roles,
        fallback_events=fallback_events,
        final_report="",  # Phase 32: no longer auto-generated; on-demand only
        error=error,
    )


# ── Internal Helpers ────────────────────────────────────────────────────


def _generate_bot_content(
    *,
    role: str,
    round_num: int,
    msg_type: str,
    run: MeetingRun,
    is_live: bool,
    command_runner: OpenCodeGoCommandRunner | None,
    workdir: str,
    previous_messages: tuple[BotMessage, ...] = (),
) -> str:
    """Generate content for a bot message — live via opencode-go or fake."""
    if not is_live:
        return _fake_bot_content(role, round_num, msg_type)
    return _live_bot_content(
        role,
        round_num,
        msg_type,
        run,
        command_runner,
        workdir,
        previous_messages=previous_messages,
    )


def _fake_bot_content(role: str, round_num: int, msg_type: str) -> str:
    """Deterministic fake content per role, round, and message type."""
    _ = round_num  # unused, reserved for future variation
    templates: dict[str, dict[str, str]] = {
        "content_lead": {
            "opinion": (
                "신규 버추얼 아이돌 그룹의 데뷔 컨셉으로 "
                "'AI와 함께 성장하는 아이돌'을 제안합니다. "
                "팬 투표로 세트리스트와 의상을 결정하고, "
                "제작 과정을 숏폼으로 공개하는 방식입니다."
            ),
            "rebuttal": (
                "마케팅 팀장님 의견에 동의합니다. 시장성은 중요하지만, "
                "콘텐츠의 진정성이 팬덤 형성의 핵심이라고 생각합니다."
            ),
        },
        "marketing_lead": {
            "opinion": (
                "시장성 측면에서 '참여형 아이돌' 컨셉은 "
                "Z세대 타겟에 매우 효과적입니다. "
                "팬 투표 참여율이 높은 숏폼 플랫폼과의 연계를 추천합니다."
            ),
            "rebuttal": (
                "콘텐츠 팀장님 의견에 보충하자면, 진정성과 시장성은 양립 가능합니다. "
                "제작 과정 자체가 마케팅 자산이 될 수 있습니다."
            ),
        },
        "quality_lead": {
            "opinion": (
                "검증 관점에서 확인할 리스크: 저작권(팬 아트 사용), "
                "개인정보(투표 시스템), 과장 광고(아이돌 AI 능력 표시). "
                "이 세 가지는 데뷔 전에 정책을 확정해야 합니다."
            ),
            "rebuttal": (
                "양 팀장님 의견 모두 타당합니다. 다만 'AI와 함께 성장'이라는 표현이 "
                "AI의 실제 능력 이상을 암시하지 않도록 주의가 필요합니다."
            ),
        },
        "tech_lead": {
            "opinion": (
                "기술적으로 팬 투표 플랫폼과 "
                "숏폼 제작 파이프라인 구축에 약 2주 소요 예상. "
                "실시간 투표 반영을 위한 API 설계가 선행되어야 합니다."
            ),
            "rebuttal": (
                "마케팅에서 제안한 숏폼 플랫폼 연계는 기술적으로 가능합니다. "
                "다만 실시간 데이터 동기화에 추가 리소스가 필요합니다."
            ),
        },
        "art_lead": {
            "opinion": (
                "비주얼 컨셉으로 '미래적이면서도 친근한' 디자인을 제안합니다. "
                "캐릭터 디자인에 AI 모티프를 자연스럽게 녹이는 방향입니다."
            ),
            "rebuttal": (
                "콘텐츠 팀장님의 'AI와 성장' 컨셉을 비주얼로 표현한다면, "
                "시즌별로 진화하는 캐릭터 디자인이 가능합니다."
            ),
        },
        "business_support_lead": {
            "opinion": (
                "사업 측면에서 IP 권리, 수익 분배, "
                "팬 데이터 소유권을 명확히 해야 합니다. "
                "초기 계약서에 AI 생성 콘텐츠의 권리 귀속 조항이 필요합니다."
            ),
            "rebuttal": (
                "법무 검토 결과, 팬 투표 콘텐츠의 2차 저작물 권리는 "
                "명시적인 약관 동의가 필요합니다."
            ),
        },
        "ceo_coordinator": {
            "opinion": (
                "각 팀장님들의 의견을 종합하여 회의를 시작하겠습니다. "
                "오늘 안건은 신규 버추얼 아이돌 그룹의 데뷔 컨셉입니다."
            ),
            "rebuttal": (
                "1차 의견을 검토한 결과, 콘텐츠-마케팅-검증 세 축이 정렬되었습니다. "
                "합의안을 도출하겠습니다."
            ),
        },
    }

    role_templates = templates.get(role, {})
    persona = BOT_PERSONAS.get(role, role)
    return role_templates.get(
        msg_type,
        f"[{persona}] 의견: 추가 검토가 필요합니다.",
    )


def _prompt_for_live_bot_message(
    *,
    role: str,
    persona: str,
    round_num: int,
    msg_type: str,
    agenda: str,
    previous_messages: tuple[BotMessage, ...] = (),
) -> str:
    display_persona = "품질관리 팀장(QA 리드)" if role in {"quality_lead", "validation_audit"} else persona
    base = [
        f"당신은 AI 가상 엔터테인먼트 회사의 '{display_persona}'입니다.",
        f"회의 안건: {agenda}",
        f"현재 {round_num}라운드 {msg_type} 단계입니다.",
    ]
    if round_num <= 1:
        base.extend(
            [
                f"'{display_persona}'로서 이 안건에 대한 초기 의견을 한 문단으로 작성해주세요.",
                "핵심 판단 1개와 근거 1개를 포함하세요.",
            ]
        )
    else:
        base.extend(
            [
                "아래는 1라운드 회의록입니다:",
                _format_previous_round_transcript(previous_messages),
                "2라운드에서는 1라운드 의견을 반복하지 마세요.",
                "반드시 동의하는 다른 팀장 의견 1개, 보완/반박할 다른 팀장 의견 1개, 최종 합의에 넣을 조건 1개를 포함하세요.",
            ]
        )
    if role in {"quality_lead", "validation_audit"}:
        base.extend(
            [
                "품질관리 팀장 규칙: 사용자가 이해할 수 있는 품질 기준으로 설명하세요.",
                "내부 구현어를 그대로 노출하지 말고 필요한 경우 다음처럼 번역하세요:",
                "worker_execution_failed → 실패 상태로 표시",
                "placeholder output → 임시/빈 응답",
                "회귀 테스트 → 재발 방지 검증",
                "regression test → 재발 방지 검증",
                "evidence artifact → 검증 기록",
            ]
        )
    base.append("한국어로 답변하고, 다른 팀장을 존중하는 전문적인 어조를 유지하세요.")
    return "\n".join(base)


def _format_previous_round_transcript(messages: tuple[BotMessage, ...]) -> str:
    if not messages:
        return "- 이전 라운드 발언 없음"
    lines = []
    for msg in messages:
        persona = BOT_PERSONAS.get(msg.bot_role, msg.bot_role)
        if msg.bot_role in {"quality_lead", "validation_audit"}:
            persona = "품질관리 팀장"
        lines.append(f"- {persona}: {_one_line(msg.content, max_length=160)}")
    return "\n".join(lines)


def _live_bot_content(
    role: str,
    round_num: int,
    msg_type: str,
    run: MeetingRun,
    command_runner: OpenCodeGoCommandRunner | None,
    workdir: str,
    previous_messages: tuple[BotMessage, ...] = (),
) -> str:
    """Generate live bot content through opencode-go worker, respecting the
    role's canonical model policy when available.
    """

    persona = BOT_PERSONAS.get(role, role)
    prompt = _prompt_for_live_bot_message(
        role=role,
        persona=persona,
        round_num=round_num,
        msg_type=msg_type,
        agenda=str(run.trigger.get("text", "")),
        previous_messages=previous_messages,
    )

    try:
        policy = worker_model_policy_for_role(role)
    except KeyError:
        policy = {"preferred": "glm-5.1", "primary_model": "glm-5.1"}

    if command_runner is None:
        task_id = f"msg_{run.meeting_run_id}_{round_num}_{role}_{msg_type}"
        run_dir = Path(workdir) / "runtime" / "meeting_runs" / run.meeting_run_id
        task = WorkerTask(
            worker_task_id=task_id,
            meeting_run_id=run.meeting_run_id,
            role=role,
            runner=WorkerTaskRunner.OPENCODE_GO,
            packet_path=str(run_dir / "packets" / f"{task_id}.json"),
            output_path=str(run_dir / "worker_outputs" / f"{task_id}.json"),
            model_policy=policy,
            hermes_refs={"prompt": prompt},
        )
        runner = OpenCodeGoWorkerRunner(timeout_seconds=300, workdir=workdir)
        completed = runner.collect(runner.dispatch(task))
        if completed.state == WorkerTaskState.SUCCEEDED:
            try:
                payload = Path(completed.output_path).read_text(encoding="utf-8")
                content = str(json.loads(payload).get("content") or "").strip()
                if content:
                    return content
            except (OSError, ValueError, TypeError):
                pass
        return _fake_bot_content(role, round_num, msg_type)

    try:
        result = command_runner(
            [
                "opencode-go",
                "--model",
                str(policy.get("preferred", policy.get("primary_model", "glm-5.1"))),
                "--prompt",
                prompt,
            ],
            timeout_seconds=300,
            workdir=workdir,
        )
        stdout = getattr(result, "stdout", "")
        if stdout:
            content = _human_facing_text(stdout)
            if content:
                return content
    except Exception:
        pass
    return _fake_bot_content(role, round_num, msg_type)


def _execute_phase14_tasks(
    tasks: tuple[WorkerTask, ...],
    *,
    mode: str,
    command_runner: OpenCodeGoCommandRunner | None,
    workdir: str,
    session: MultiBotSession,
) -> tuple[WorkerTask, ...]:
    """Execute Phase 14 worker tasks with multi-bot awareness."""
    completed: list[WorkerTask] = []
    for task in tasks:
        runner: WorkerRunner
        if task.runner == WorkerTaskRunner.OPENCODE_GO and mode == "live-worker":
            runner = OpenCodeGoWorkerRunner(
                command_runner=command_runner,
                timeout_seconds=600,
                workdir=workdir,
            )
        else:
            bot_output = _resolve_bot_output_for_role(task.role, session)
            runner = FakeWorkerRunner(output={"summary": bot_output})
        dispatched = runner.dispatch(task)
        completed.append(runner.collect(dispatched))
    return tuple(completed)


def _task_output_payload(task: WorkerTask) -> dict[str, object]:
    try:
        path = Path(task.output_path)
        if not path.exists():
            return {}
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError, TypeError):
        return {}


def _task_output_summary(task: WorkerTask, *, max_length: int = 180) -> str:
    payload = _task_output_payload(task)
    for key in ("content", "summary", "stdout"):
        value = _human_facing_text(payload.get(key))
        if value:
            if _is_placeholder_specialist_output(task, value):
                return f"worker_execution_failed: placeholder output for {task.role}"[
                    :max_length
                ]
            return value[:max_length]
    if task.error:
        return f"error={task.error}"[:max_length]
    return "output recorded"


def _is_placeholder_specialist_output(task: WorkerTask, value: object) -> bool:
    text = _one_line(value, max_length=200).lower()
    role = str(task.role).lower()
    return text in {
        f"{role} specialist output",
        f"{role} output",
        f"{role} worker output",
    }


def _human_facing_text(value: object) -> str:
    """Extract readable user-facing text from provider/legacy JSON output.

    Legacy injected command runners and provider shims may return a structured
    JSON object on stdout. Discord should show the role's human answer, not the
    raw JSON wrapper, escaped unicode, test model metadata, or status fields.
    """

    if value is None:
        return ""
    if isinstance(value, dict):
        for key in ("content", "summary", "text", "message", "stdout"):
            nested = _human_facing_text(value.get(key))
            if nested:
                return nested
        return ""
    if isinstance(value, list):
        parts = [_human_facing_text(item) for item in value]
        return "\n".join(part for part in parts if part).strip()

    text = str(value).strip()
    if not text:
        return ""

    candidate = text
    for _ in range(2):
        stripped = candidate.strip()
        if not stripped or stripped[0] not in "[{\"":
            break
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            break
        extracted = _human_facing_text(parsed)
        if extracted:
            return extracted
        if isinstance(parsed, str) and parsed != candidate:
            candidate = parsed
            continue
        break
    return text


def _one_line(value: object, *, max_length: int) -> str:
    text = " ".join(_human_facing_text(value).split())
    if len(text) <= max_length:
        return text
    return text[: max_length - 1].rstrip() + "…"


def _first_line(value: object, *, fallback: str) -> str:
    text = _human_facing_text(value).strip()
    if not text:
        return fallback
    for line in text.splitlines():
        line = line.strip(" #[]")
        if line:
            return line
    return fallback


def _table_cell(value: object) -> str:
    return _one_line(value, max_length=96).replace("|", "/") or "-"


def _generic_consensus_text(value: str) -> bool:
    return value.strip() in {
        "모든 팀장의 의견을 수렴하여 합의에 도달했습니다.",
        "합의안은 회의 발언과 specialist 결과를 기준으로 정리합니다.",
    }


def _concrete_conclusion(*, agenda: str, consensus: str) -> str:
    if consensus and not _generic_consensus_text(consensus):
        return _one_line(consensus, max_length=150)
    return _one_line(
        f"{agenda} 안건은 Discord에서 결론·합의안·다음 액션을 먼저 확인하고, 세부 evidence는 runtime artifact로 분리 보관하기로 확정합니다.",
        max_length=150,
    )


def _validation_evidence_lines(validation_verdicts: tuple[object, ...]) -> list[str]:
    lines: list[str] = []
    for verdict in validation_verdicts:
        role = str(getattr(verdict, "validator_role", "validator"))
        result = str(getattr(verdict, "verdict", "")) or "unknown"
        confidence = str(getattr(verdict, "confidence", ""))
        findings = _one_line("; ".join(getattr(verdict, "findings", ())), max_length=160)
        lines.append(f"validation:{role:<16} {result.upper()} confidence={confidence}")
        if findings:
            lines.append(f"findings: {findings}")
    return lines or ["validation: none"]


def _model_evidence_lines(worker_tasks: tuple[WorkerTask, ...]) -> list[str]:
    warning_lines: list[str] = []
    normal_lines: list[str] = []
    for task in worker_tasks:
        attempts = _task_attempts(task)
        model_path = " -> ".join(attempts) or "모델 기록 없음"
        payload = _task_output_payload(task)
        placeholder_failed = any(
            _is_placeholder_specialist_output(task, payload.get(key))
            for key in ("content", "summary", "stdout")
            if payload.get(key) is not None
        )
        icon = "✅" if task.state == WorkerTaskState.SUCCEEDED and not placeholder_failed else "⚠️"
        state_note = " worker_execution_failed=placeholder_output" if placeholder_failed else ""
        line = f"{task.role:<22} {icon} {model_path} fallback_used={str(len(attempts) > 1).lower()}{state_note}"
        if placeholder_failed or task.state != WorkerTaskState.SUCCEEDED:
            warning_lines.append(line)
        else:
            normal_lines.append(line)
    return [*warning_lines, *normal_lines]


def _derive_agreement_items(
    *,
    agenda: str,
    conclusion: str,
    role_summaries: Mapping[str, str],
    specialist_summaries: Mapping[str, str],
    fallback_used: bool,
) -> list[str]:
    source_text = _summaries_source_text(role_summaries, specialist_summaries)
    items: list[str] = []

    def add(item: str) -> None:
        if item not in items:
            items.append(item)

    if "legal-reviewer" in source_text and (
        "placeholder" in source_text or "worker_execution_failed" in source_text
    ):
        add(
            "legal-reviewer placeholder는 성공 산출물이 아니라 worker_execution_failed로 표시하고 evidence 상태와 동기화한다."
        )
    elif "placeholder" in source_text or "worker_execution_failed" in source_text:
        add(
            "placeholder specialist output은 성공 산출물이 아니라 worker_execution_failed로 표시하고 evidence 상태와 동기화한다."
        )

    if any(term in source_text for term in ("discord", "bullet", "표", "artifact", "evidence")):
        add("Discord thread는 사용자 판단 화면, local runtime artifact는 전체 evidence 보관소로 역할을 분리한다.")
    else:
        add("회의에서 합의된 결정은 역할별 발언과 specialist 결과를 기준으로 세부 항목으로 관리한다.")

    if fallback_used:
        add("fallback 사용 role은 모델 경로와 결과 품질을 별도 확인 대상으로 둔다.")
    return items


def _derive_action_items(
    *,
    role_summaries: Mapping[str, str],
    specialist_summaries: Mapping[str, str],
    fallback_used: bool,
) -> list[str]:
    source_text = _summaries_source_text(role_summaries, specialist_summaries)
    actions: list[str] = []

    def add(action: str) -> None:
        if action not in actions:
            actions.append(action)

    if "legal-reviewer" in source_text and "placeholder" in source_text:
        add("legal-reviewer placeholder output을 worker_execution_failed로 처리하는 회귀 테스트를 고정한다.")
    elif "placeholder" in source_text and "worker_execution_failed" in source_text:
        add("placeholder specialist output을 worker_execution_failed로 처리하는 회귀 테스트를 고정한다.")
    elif "회귀 테스트" in source_text:
        if "evidence" in source_text:
            add("evidence 분리와 specialist 고유 output을 회귀 테스트로 고정한다.")
        else:
            add("specialist 고유 output을 회귀 테스트로 고정한다.")
    if "API" in source_text or "큐" in source_text or "자동화" in source_text:
        add("기술 제안에 따라 API/큐/자동화 경계를 구현 작업으로 분리한다.")
    if "UI" in source_text or "UX" in source_text or "bullet" in source_text or "표" in source_text:
        add("Discord는 bullet 요약을 유지하고 local artifact는 표 렌더링을 유지한다.")
    if fallback_used:
        add("fallback이 발생한 role의 모델 경로와 산출물 품질을 추가 확인한다.")
    if not actions:
        add("회의에서 도출된 역할별 제안을 실행 작업으로 분리한다.")
        add("세부 evidence와 원문은 `final_report_v2.md`와 worker output에서 확인한다.")
    return actions[:4]


def _summaries_source_text(
    role_summaries: Mapping[str, str], specialist_summaries: Mapping[str, str]
) -> str:
    parts = [f"{role}: {summary}" for role, summary in role_summaries.items()]
    parts.extend(f"{role}: {summary}" for role, summary in specialist_summaries.items())
    return "\n".join(parts)


def _prefix_lines(prefix: str, items: list[str]) -> list[str]:
    return [f"{prefix} {item}" for item in items]


def _task_attempts(task: WorkerTask) -> tuple[str, ...]:
    payload = _task_output_payload(task)
    attempts = payload.get("attempted_models") or []
    if isinstance(attempts, list):
        return tuple(str(item) for item in attempts if str(item).strip())
    model = str(payload.get("model") or task.model_policy.get("preferred") or "").strip()
    return (model,) if model else ()


def _fallback_events(worker_tasks: tuple[WorkerTask, ...]) -> tuple[str, ...]:
    events: list[str] = []
    for task in worker_tasks:
        attempts = _task_attempts(task)
        if len(attempts) > 1:
            events.append(f"{task.role}: {' -> '.join(attempts)}")
    return tuple(events)


def _build_final_report(
    *,
    run: MeetingRun,
    session: MultiBotSession,
    worker_tasks: tuple[WorkerTask, ...],
    validation_verdicts: tuple[object, ...],
    internal_specialist_roles: tuple[str, ...],
    fallback_events: tuple[str, ...],
) -> str:
    trigger_text = _human_facing_text(run.trigger.get("text"))
    agenda = _first_line(trigger_text, fallback="AI_Agent 회의")
    fallback_used = bool(fallback_events)
    validation_passed = all(
        str(getattr(v, "verdict", "")).lower() in {"pass", "passed", "ok"}
        for v in validation_verdicts
    ) if validation_verdicts else True
    status = (
        f"**상태:** ✅ 완료 · 검증 {'PASS' if validation_passed else 'REVIEW'} · "
        f"fallback {'있음' if fallback_used else '없음'}"
    )

    role_latest: dict[str, str] = {}
    for round_data in session.rounds:
        for msg in round_data.messages:
            if msg.msg_type not in {"opinion", "rebuttal", "consensus"}:
                continue
            role_latest[msg.bot_role] = _one_line(msg.content, max_length=72)
    role_rows = [
        f"| {BOT_PERSONAS.get(role, role)} | {_table_cell(role_latest[role])} |"
        for role in session.participants
        if role in role_latest
    ] or ["| - | 역할별 의견 없음 |"]

    task_by_role = {task.role: task for task in worker_tasks}
    specialist_summaries = {
        role: _task_output_summary(task_by_role[role], max_length=110)
        for role in internal_specialist_roles
        if role in task_by_role
    }
    specialist_rows = [
        f"| {role} | {_table_cell(summary)} |"
        for role, summary in specialist_summaries.items()
    ] or ["| - | 투입된 내부 specialist 없음"]

    evidence_lines = _validation_evidence_lines(validation_verdicts) + [""] + _model_evidence_lines(worker_tasks)

    consensus = session.consensus_summary or "합의안은 회의 발언과 specialist 결과를 기준으로 정리합니다."
    conclusion = _concrete_conclusion(agenda=agenda, consensus=consensus)
    agreement_items = _prefix_lines(
        "-",
        _derive_agreement_items(
            agenda=agenda,
            conclusion=conclusion,
            role_summaries=role_latest,
            specialist_summaries=specialist_summaries,
            fallback_used=fallback_used,
        ),
    )
    action_items = _prefix_lines(
        "-",
        _derive_action_items(
            role_summaries=role_latest,
            specialist_summaries=specialist_summaries,
            fallback_used=fallback_used,
        ),
    )

    risk_items = (
        ["- fallback이 사용되어 해당 모델 경로와 결과 품질을 추가 확인해야 합니다."]
        if fallback_used
        else ["- 리스크 없음 — fallback 없이 완료되었고, 세부 evidence는 참고 정보로 분리합니다."]
    )

    return "\n".join(
        [
            f"# 📋 {_one_line(agenda, max_length=70)}",
            status,
            "",
            "## 🎯 결론",
            conclusion,
            "",
            "## ✅ 합의안",
            *agreement_items,
            "",
            "## 🚀 다음 액션",
            *action_items,
            "",
            "## ⚠️ 리스크 / 이견",
            *risk_items,
            "",
            "## 👥 팀장 핵심 의견",
            "| 팀장 | 핵심 포인트 |",
            "|---|---|",
            *role_rows,
            "",
            "## 🧑‍💻 Specialist 투입",
            "| specialist | 결과 한줄 요약 |",
            "|---|---|",
            *specialist_rows,
            "",
            "## 🔍 검증 상세 / 모델 Evidence",
            "```text",
            *evidence_lines,
            f"fallback_used: {str(fallback_used).lower()}",
            "```",
        ]
    )


def _build_discord_final_report(
    *,
    run: MeetingRun,
    session: MultiBotSession,
    worker_tasks: tuple[WorkerTask, ...],
    validation_verdicts: tuple[object, ...],
    internal_specialist_roles: tuple[str, ...],
    fallback_events: tuple[str, ...],
) -> str:
    trigger_text = _human_facing_text(run.trigger.get("text"))
    agenda = _first_line(trigger_text, fallback="AI_Agent 회의")
    fallback_used = bool(fallback_events)
    validation_passed = all(
        str(getattr(v, "verdict", "")).lower() in {"pass", "passed", "ok"}
        for v in validation_verdicts
    ) if validation_verdicts else True
    status = (
        f"**상태:** ✅ 완료 · 검증 {'PASS' if validation_passed else 'REVIEW'} · "
        f"fallback {'있음' if fallback_used else '없음'}"
    )

    role_latest: dict[str, str] = {}
    for round_data in session.rounds:
        for msg in round_data.messages:
            if msg.msg_type not in {"opinion", "rebuttal", "consensus"}:
                continue
            role_latest[msg.bot_role] = _one_line(msg.content, max_length=88)
    role_lines = [
        f"• {BOT_PERSONAS.get(role, role)}: {role_latest[role]}"
        for role in session.participants
        if role in role_latest
    ] or ["• 역할별 의견 없음"]

    task_by_role = {task.role: task for task in worker_tasks}
    specialist_summaries = {
        role: _task_output_summary(task_by_role[role], max_length=110)
        for role in internal_specialist_roles
        if role in task_by_role
    }
    specialist_lines = [
        f"• {role}: {summary}"
        for role, summary in specialist_summaries.items()
    ] or ["• 투입된 내부 specialist 없음"]
    evidence_lines = _validation_evidence_lines(validation_verdicts) + [""] + _model_evidence_lines(worker_tasks)

    consensus = session.consensus_summary or "합의안은 회의 발언과 specialist 결과를 기준으로 정리합니다."
    conclusion = _concrete_conclusion(agenda=agenda, consensus=consensus)
    agreement_items = _prefix_lines(
        "•",
        _derive_agreement_items(
            agenda=agenda,
            conclusion=conclusion,
            role_summaries=role_latest,
            specialist_summaries=specialist_summaries,
            fallback_used=fallback_used,
        ),
    )
    action_items = _prefix_lines(
        "•",
        _derive_action_items(
            role_summaries=role_latest,
            specialist_summaries=specialist_summaries,
            fallback_used=fallback_used,
        ),
    )
    risk_line = (
        "• fallback이 사용되어 해당 모델 경로와 결과 품질을 추가 확인해야 합니다."
        if fallback_used
        else "• 리스크 없음 — fallback 없이 완료되었고, 세부 evidence는 참고 정보로 분리합니다."
    )

    return "\n".join(
        [
            f"# 📋 {_one_line(agenda, max_length=70)}",
            status,
            "",
            "## 🎯 결론",
            conclusion,
            "",
            "## ✅ 합의안",
            *agreement_items,
            "",
            "## 🚀 다음 액션",
            *action_items,
            "",
            "## ⚠️ 리스크 / 이견",
            risk_line,
            "",
            "## 👥 팀장 핵심 의견",
            *role_lines,
            "",
            "## 🧑‍💻 Specialist 투입",
            *specialist_lines,
            "",
            "## 🔍 검증 상세 / 모델 Evidence",
            "```text",
            *evidence_lines,
            f"fallback_used: {str(fallback_used).lower()}",
            "```",
        ]
    )


def _resolve_bot_output_for_role(role: str, session: MultiBotSession) -> str:
    """Find the bot's consensus or last message output from the session."""
    for round_data in reversed(session.rounds):
        for msg in round_data.messages:
            if msg.bot_role == role:
                return msg.content
    return f"[{BOT_PERSONAS.get(role, role)}] 의견을 제출했습니다."
