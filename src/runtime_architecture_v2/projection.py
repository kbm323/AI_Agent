"""Discord projection layer for Runtime Architecture v2.

This module keeps Discord-facing output as an AI_Agent domain projection. It
formats safe user-facing messages and records fake sink publications without
reimplementing Hermes Gateway, queues, or Discord interaction infrastructure.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from src.runtime_architecture_v2.discord_channels import (
    current_discord_home_channel_ids_by_profile,
)
from src.runtime_architecture_v2.schemas import (
    DiscordProjectionEvent,
    MeetingRun,
    MeetingRunState,
    RoutingResult,
    ValidationVerdict,
)

_DISCORD_CONTENT_LIMIT = 2000
_SAFE_MENTION_BREAK = "\u000b"
_DEFAULT_ROLE_ORDER = (
    "ceo_coordinator",
    "content_lead",
    "art_lead",
    "tech_lead",
    "marketing_lead",
    "business_support_lead",
    "validation_audit",
)
_FORBIDDEN_ROLE_FRAGMENTS = ("research_lead", "personal_assistant", "openclaw")
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)(\b(?:api[_-]?key|token|password|credential)\b['\"]?\s*[:=]\s*['\"]?)"
    r"([^\s,'\"}]+)"
)
_BEARER_SECRET_RE = re.compile(r"(?i)\bbearer\s+\S+")
_DISCORD_USER_AGENT = (
    "DiscordBot (https://github.com/kbm323/AI_Agent, phase12-live-smoke) Python/urllib"
)


def _normalize_role_name(role: str) -> str:
    return role.strip().lower().replace("-", "_").replace(" ", "_")


def _is_forbidden_role(role: str) -> bool:
    normalized = _normalize_role_name(role)
    return any(fragment in normalized for fragment in _FORBIDDEN_ROLE_FRAGMENTS)


@dataclass(frozen=True)
class TeamBotTopology:
    """Stable Discord-facing company bot role mapping."""

    roles: tuple[str, ...]
    team_to_role: dict[str, str] = field(default_factory=dict)
    fallback_role: str = "ceo_coordinator"

    def __post_init__(self) -> None:
        roles = tuple(self.roles)
        if not roles:
            raise ValueError("topology requires at least one bot role")
        mapped_roles = tuple(self.team_to_role.values())
        candidates = (*roles, self.fallback_role, *mapped_roles)
        forbidden = [role for role in candidates if _is_forbidden_role(role)]
        if forbidden:
            raise ValueError(f"forbidden bot roles: {', '.join(forbidden)}")
        unknown_mapped_roles = sorted(set(mapped_roles) - set(roles))
        if unknown_mapped_roles:
            raise ValueError(
                f"unknown mapped bot role: {', '.join(unknown_mapped_roles)}"
            )
        if self.fallback_role not in roles:
            raise ValueError(f"unknown fallback bot role: {self.fallback_role}")
        object.__setattr__(self, "roles", roles)
        object.__setattr__(self, "team_to_role", dict(self.team_to_role))

    def role_for_team(self, team: str) -> str:
        """Return the Discord bot role for a routed team."""

        return self.team_to_role.get(team, self.fallback_role)

    def to_dict(self) -> dict[str, object]:
        return {
            "roles": list(self.roles),
            "team_to_role": self.team_to_role,
            "fallback_role": self.fallback_role,
        }


def default_team_bot_topology() -> TeamBotTopology:
    """Return the stable 7-bot company projection topology."""

    return TeamBotTopology(
        roles=_DEFAULT_ROLE_ORDER,
        team_to_role={role: role for role in _DEFAULT_ROLE_ORDER},
    )


@dataclass(frozen=True)
class ProjectionPublishResult:
    """Result of publishing a projection event through a sink."""

    event_id: str
    status: str
    discord_message_id: str = ""
    error: str = ""


@dataclass(frozen=True)
class ThreadCreateResult:
    """Result of creating a live Discord meeting thread."""

    status: str
    thread_id: str = ""
    error: str = ""


@dataclass(frozen=True)
class DiscordBoundaryDecision:
    """Allowlist decision for a live Discord projection boundary."""

    allowed: bool
    reason: str


@dataclass(frozen=True)
class DiscordLiveBoundaryPolicy:
    """Phase 24 live Discord boundary policy.

    This is an inventory/allowlist guard, not a permission mutation layer. It
    preserves the currently verified Hermes-first posture: mention-gated bots,
    no free-response channels, no Administrator, and no Discord permission
    changes unless a later explicit live need is approved.

    The optional ``channel_resolver`` maps symbolic channel identifiers (e.g.
    ``"home:aicompanyceo:#회의실-전략결정"``) to real Discord snowflake IDs at
    eval time. When ``None`` (default), symbolic names are compared as-is.
    """

    guild_id: str
    allowed_channel_ids_by_profile: dict[str, str]
    permission_mutation_allowed: bool = False
    administrator_allowed: bool = False
    require_mention: bool = True
    thread_require_mention: bool = True
    free_response_channels: tuple[str, ...] = ()
    channel_resolver: Callable[[str], str] | None = None

    @classmethod
    def current_verified(cls) -> DiscordLiveBoundaryPolicy:
        """Return the current verified 7-bot Entertainment boundary."""

        return cls(
            guild_id="1505600166676271244",
            allowed_channel_ids_by_profile=current_discord_home_channel_ids_by_profile(),
        )

    def evaluate(
        self,
        *,
        profile: str,
        guild_id: str,
        channel_id: str,
    ) -> DiscordBoundaryDecision:
        """Fail closed unless guild/profile/channel match the allowlist."""

        if guild_id != self.guild_id:
            return DiscordBoundaryDecision(False, "guild_not_allowed")
        allowed_channel_id = self.allowed_channel_ids_by_profile.get(profile)
        if allowed_channel_id is None:
            return DiscordBoundaryDecision(False, "profile_not_allowed")
        resolved_allowed = (
            self.channel_resolver(allowed_channel_id)
            if self.channel_resolver
            else allowed_channel_id
        )
        resolved_incoming = (
            self.channel_resolver(channel_id) if self.channel_resolver else channel_id
        )
        if resolved_incoming != resolved_allowed:
            return DiscordBoundaryDecision(False, "channel_not_allowed")
        if self.permission_mutation_allowed:
            return DiscordBoundaryDecision(False, "permission_mutation_not_allowed")
        if self.administrator_allowed:
            return DiscordBoundaryDecision(False, "administrator_not_allowed")
        if not self.require_mention or not self.thread_require_mention:
            return DiscordBoundaryDecision(False, "mention_gate_required")
        if self.free_response_channels:
            return DiscordBoundaryDecision(False, "free_response_not_allowed")
        return DiscordBoundaryDecision(True, "allowed")


@dataclass(frozen=True)
class SharedMeetingThreadProjectionPolicy:
    """Allow verified team-lead profiles to post into one CEO-owned meeting thread."""

    boundary_policy: DiscordLiveBoundaryPolicy
    parent_channel_id: str
    thread_id: str
    owner_profile: str = "aicompanyceo"

    def evaluate(
        self,
        *,
        profile: str,
        guild_id: str,
        parent_channel_id: str,
        thread_id: str,
    ) -> DiscordBoundaryDecision:
        if guild_id != self.boundary_policy.guild_id:
            return DiscordBoundaryDecision(False, "guild_not_allowed")
        if profile not in self.boundary_policy.allowed_channel_ids_by_profile:
            return DiscordBoundaryDecision(False, "profile_not_allowed")
        verified_parent = self.boundary_policy.allowed_channel_ids_by_profile.get(
            self.owner_profile
        )
        if parent_channel_id != self.parent_channel_id:
            return DiscordBoundaryDecision(False, "shared_parent_mismatch")
        if parent_channel_id != verified_parent:
            return DiscordBoundaryDecision(False, "shared_parent_not_allowed")
        if thread_id != self.thread_id:
            return DiscordBoundaryDecision(False, "shared_thread_mismatch")
        if self.boundary_policy.permission_mutation_allowed:
            return DiscordBoundaryDecision(False, "permission_mutation_not_allowed")
        if self.boundary_policy.administrator_allowed:
            return DiscordBoundaryDecision(False, "administrator_not_allowed")
        if (
            not self.boundary_policy.require_mention
            or not self.boundary_policy.thread_require_mention
        ):
            return DiscordBoundaryDecision(False, "mention_gate_required")
        if self.boundary_policy.free_response_channels:
            return DiscordBoundaryDecision(False, "free_response_not_allowed")
        return DiscordBoundaryDecision(True, "allowed")


def _sanitize_discord_content(content: str) -> str:
    sanitized = _BEARER_SECRET_RE.sub("bearer [redacted]", content)
    sanitized = _SECRET_ASSIGNMENT_RE.sub(r"\1[redacted]", sanitized)
    sanitized = sanitized.replace("@everyone", f"@{_SAFE_MENTION_BREAK}everyone")
    sanitized = sanitized.replace("@here", f"@{_SAFE_MENTION_BREAK}here")
    return sanitized[:_DISCORD_CONTENT_LIMIT]


def _sanitize_discord_thread_name(name: str) -> str:
    sanitized = _sanitize_discord_content(name).replace("\n", " ").strip()
    return sanitized[:100]


def _bullet_list(label: str, values: Iterable[str]) -> str:
    items = tuple(value for value in values if value)
    if not items:
        return f"- {label}: none"
    return f"- {label}: {', '.join(items)}"


class DiscordProjectionFormatter:
    """Build Discord-safe projection events from MeetingRun domain objects."""

    def __init__(self, topology: TeamBotTopology | None = None) -> None:
        self._topology = topology or default_team_bot_topology()

    def build_summary_event(
        self,
        *,
        event_id: str,
        run: MeetingRun,
        state: MeetingRunState | str,
        routing: RoutingResult | None,
        verdicts: tuple[ValidationVerdict, ...] = (),
        target_channel_id: str,
        target_thread_id: str = "",
        raw_worker_outputs: tuple[str, ...] = (),
    ) -> DiscordProjectionEvent:
        """Build a user-facing MeetingRun summary without raw worker dumps."""

        del raw_worker_outputs
        state_text = state.value if isinstance(state, MeetingRunState) else str(state)
        teams = routing.teams if routing else ()
        validators = routing.validators if routing else ()
        verdict_values = tuple(str(verdict.verdict) for verdict in verdicts)
        primary_role = (
            self._topology.role_for_team(teams[0]) if teams else "ceo_coordinator"
        )
        if primary_role != "validation_audit":
            primary_role = "ceo_coordinator"

        trigger_text = str(run.trigger.get("text") or "")
        lines = [
            f"MeetingRun {run.meeting_run_id} projection",
            f"- state: {state_text}",
            f"- priority: {run.priority}",
            f"- trigger: {trigger_text}",
            _bullet_list("teams", teams),
            _bullet_list("validators", validators),
            _bullet_list("verdicts", verdict_values),
        ]
        if routing and routing.rationale:
            lines.append(f"- rationale: {routing.rationale}")
        lines.append("- raw_worker_outputs: omitted")
        content = _sanitize_discord_content("\n".join(lines))
        return DiscordProjectionEvent(
            event_id=event_id,
            meeting_run_id=run.meeting_run_id,
            bot_role=primary_role,
            target_channel_id=target_channel_id,
            target_thread_id=target_thread_id,
            content=content,
            source="meeting_run",
            source_id=run.meeting_run_id,
        )

    def build_validation_event(
        self,
        *,
        event_id: str,
        verdict: ValidationVerdict,
        target_channel_id: str,
        target_thread_id: str = "",
    ) -> DiscordProjectionEvent:
        """Build a validation/audit projection event."""

        lines = [
            f"Validation verdict for {verdict.meeting_run_id}",
            f"- validator: {verdict.validator_role}",
            f"- model: {verdict.validator_model}",
            f"- verdict: {verdict.verdict}",
            f"- confidence: {verdict.confidence:.2f}",
            _bullet_list("findings", verdict.findings),
            _bullet_list("required_actions", verdict.required_actions),
        ]
        return DiscordProjectionEvent(
            event_id=event_id,
            meeting_run_id=verdict.meeting_run_id,
            bot_role="validation_audit",
            target_channel_id=target_channel_id,
            target_thread_id=target_thread_id,
            content=_sanitize_discord_content("\n".join(lines)),
            source="validation_verdict",
            source_id=verdict.validation_id,
        )


class FakeDiscordProjectionSink:
    """In-memory idempotent sink for projection tests and fake simulations."""

    def __init__(self) -> None:
        self._events: dict[str, DiscordProjectionEvent] = {}
        self._message_ids: dict[str, str] = {}

    @property
    def events(self) -> tuple[DiscordProjectionEvent, ...]:
        return tuple(self._events.values())

    def publish(self, event: DiscordProjectionEvent) -> ProjectionPublishResult:
        if not event.content.strip():
            return ProjectionPublishResult(
                event_id=event.event_id,
                status="rejected",
                error="content must not be empty",
            )
        if event.event_id in self._events:
            return ProjectionPublishResult(
                event_id=event.event_id,
                status="duplicate",
                discord_message_id=self._message_ids[event.event_id],
            )
        message_id = f"fake-discord-message:{event.event_id}"
        self._events[event.event_id] = event
        self._message_ids[event.event_id] = message_id
        return ProjectionPublishResult(
            event_id=event.event_id,
            status="published",
            discord_message_id=message_id,
        )


def _default_discord_http_post(
    url: str,
    *,
    headers: Mapping[str, str],
    json_body: Mapping[str, object],
    timeout_seconds: int,
) -> dict[str, object]:
    request_headers = {
        "User-Agent": _DISCORD_USER_AGENT,
        **dict(headers),
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(json_body).encode("utf-8"),
        headers=request_headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            body = response.read().decode("utf-8")
            try:
                payload = json.loads(body) if body else {}
            except json.JSONDecodeError:
                payload = {}
            return {"status_code": response.status, "json": payload, "text": body}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        return {"status_code": exc.code, "text": body}
    except OSError as exc:
        return {"status_code": 0, "text": str(exc)}


class LiveDiscordProjectionSink:
    """Discord REST projection sink behind the same publish interface.

    Tokens are read from the injected environment mapping only. Tests inject the
    HTTP callable, so unit tests never touch live Discord.
    """

    def __init__(
        self,
        *,
        env: Mapping[str, str] | None = None,
        http_post: Callable[..., Mapping[str, Any]] | None = None,
        api_base_url: str = "https://discord.com/api/v10",
        timeout_seconds: int = 15,
        boundary_policy: DiscordLiveBoundaryPolicy | None = None,
        shared_thread_policy: SharedMeetingThreadProjectionPolicy | None = None,
        profile: str = "",
        guild_id: str = "",
    ) -> None:
        self.env = {} if env is None else env
        self.http_post = http_post
        self.api_base_url = api_base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.boundary_policy = boundary_policy
        self.shared_thread_policy = shared_thread_policy
        self.profile = profile
        self.guild_id = guild_id

    def publish(self, event: DiscordProjectionEvent) -> ProjectionPublishResult:
        if not event.content.strip():
            return ProjectionPublishResult(
                event_id=event.event_id,
                status="rejected",
                error="content must not be empty",
            )
        if self.http_post is None:
            return ProjectionPublishResult(
                event_id=event.event_id,
                status="blocked",
                error="live_http_client_required",
            )
        post_channel_id = event.target_thread_id or event.target_channel_id
        if event.target_thread_id and self.shared_thread_policy is not None:
            decision = self.shared_thread_policy.evaluate(
                profile=self.profile,
                guild_id=self.guild_id,
                parent_channel_id=event.target_channel_id,
                thread_id=event.target_thread_id,
            )
            if not decision.allowed:
                return ProjectionPublishResult(
                    event_id=event.event_id,
                    status="blocked",
                    error=decision.reason,
                )
        elif self.boundary_policy is not None:
            decision = self.boundary_policy.evaluate(
                profile=self.profile,
                guild_id=self.guild_id,
                channel_id=event.target_channel_id,
            )
            if not decision.allowed:
                return ProjectionPublishResult(
                    event_id=event.event_id,
                    status="blocked",
                    error=decision.reason,
                )
        token = self.env.get("DISCORD_BOT_TOKEN", "").strip()
        if not token:
            return ProjectionPublishResult(
                event_id=event.event_id,
                status="blocked",
                error="missing_discord_bot_token",
            )
        try:
            response = self.http_post(
                f"{self.api_base_url}/channels/{post_channel_id}/messages",
                headers={
                    "Authorization": f"Bot {token}",
                    "Content-Type": "application/json",
                },
                json_body={
                    "content": _sanitize_discord_content(event.content),
                    "allowed_mentions": {"parse": []},
                },
                timeout_seconds=self.timeout_seconds,
            )
        except Exception:
            return ProjectionPublishResult(
                event_id=event.event_id,
                status="failed",
                error="discord_http_exception",
            )
        if not isinstance(response, Mapping):
            return ProjectionPublishResult(
                event_id=event.event_id,
                status="failed",
                error="discord_http_invalid_response",
            )
        try:
            raw_status = response.get("status_code")
            status_code = int(raw_status) if raw_status is not None else 0
        except (TypeError, ValueError):
            return ProjectionPublishResult(
                event_id=event.event_id,
                status="failed",
                error="discord_http_invalid_status",
            )
        if not 200 <= status_code < 300:
            return ProjectionPublishResult(
                event_id=event.event_id,
                status="failed",
                error=f"discord_http_{status_code}",
            )
        payload = response.get("json") or {}
        message_id = str(payload.get("id") if isinstance(payload, Mapping) else "")
        return ProjectionPublishResult(
            event_id=event.event_id,
            status="published",
            discord_message_id=message_id,
        )


class LiveDiscordThreadManager:
    """Create live Discord meeting threads through an injected HTTP client."""

    def __init__(
        self,
        *,
        env: Mapping[str, str] | None = None,
        http_post: Callable[..., Mapping[str, Any]] | None = None,
        api_base_url: str = "https://discord.com/api/v10",
        timeout_seconds: int = 15,
        boundary_policy: DiscordLiveBoundaryPolicy | None = None,
        profile: str = "aicompanyceo",
        guild_id: str = "",
    ) -> None:
        self.env = {} if env is None else env
        self.http_post = http_post
        self.api_base_url = api_base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.boundary_policy = boundary_policy
        self.profile = profile
        self.guild_id = guild_id

    def create_meeting_thread(
        self,
        *,
        parent_channel_id: str,
        name: str,
        auto_archive_duration: int = 60,
    ) -> ThreadCreateResult:
        thread_name = _sanitize_discord_thread_name(name)
        if not parent_channel_id.strip():
            return ThreadCreateResult(
                status="rejected", error="parent_channel_required"
            )
        if not thread_name:
            return ThreadCreateResult(status="rejected", error="thread_name_required")
        if self.http_post is None:
            return ThreadCreateResult(
                status="blocked", error="live_http_client_required"
            )
        if self.boundary_policy is not None:
            decision = self.boundary_policy.evaluate(
                profile=self.profile,
                guild_id=self.guild_id,
                channel_id=parent_channel_id,
            )
            if not decision.allowed:
                return ThreadCreateResult(status="blocked", error=decision.reason)
        token = self.env.get("DISCORD_BOT_TOKEN", "").strip()
        if not token:
            return ThreadCreateResult(
                status="blocked", error="missing_discord_bot_token"
            )
        try:
            response = self.http_post(
                f"{self.api_base_url}/channels/{parent_channel_id}/threads",
                headers={
                    "Authorization": f"Bot {token}",
                    "Content-Type": "application/json",
                },
                json_body={
                    "name": thread_name,
                    "type": 11,
                    "auto_archive_duration": auto_archive_duration,
                    "invitable": False,
                },
                timeout_seconds=self.timeout_seconds,
            )
        except Exception:
            return ThreadCreateResult(status="failed", error="discord_http_exception")
        if not isinstance(response, Mapping):
            return ThreadCreateResult(
                status="failed", error="discord_http_invalid_response"
            )
        try:
            raw_status = response.get("status_code")
            status_code = int(raw_status) if raw_status is not None else 0
        except (TypeError, ValueError):
            return ThreadCreateResult(
                status="failed", error="discord_http_invalid_status"
            )
        if not 200 <= status_code < 300:
            return ThreadCreateResult(
                status="failed", error=f"discord_http_{status_code}"
            )
        payload = response.get("json") or {}
        thread_id = str(payload.get("id") if isinstance(payload, Mapping) else "")
        if not thread_id:
            return ThreadCreateResult(
                status="failed", error="discord_thread_id_missing"
            )
        return ThreadCreateResult(status="created", thread_id=thread_id)


@dataclass(frozen=True)
class HermesCommandSurfacePolicy:
    """Policy documenting that Phase 6 uses Hermes-native command ingress."""

    command_mode: str
    accepts_mention_trigger: bool
    accepts_slash_command: bool
    requires_custom_interaction_endpoint: bool
    requires_custom_queue_db: bool

    @classmethod
    def default(cls) -> HermesCommandSurfacePolicy:
        return cls(
            command_mode="hermes_native",
            accepts_mention_trigger=True,
            accepts_slash_command=False,
            requires_custom_interaction_endpoint=False,
            requires_custom_queue_db=False,
        )

    def describe(self) -> str:
        return (
            "Hermes Gateway mention-based command surface; "
            "AI_Agent owns MeetingRun projection events only. "
            "Custom Discord interactions and local queue storage are out of scope."
        )


__all__ = [
    "DiscordBoundaryDecision",
    "DiscordLiveBoundaryPolicy",
    "DiscordProjectionFormatter",
    "FakeDiscordProjectionSink",
    "HermesCommandSurfacePolicy",
    "LiveDiscordProjectionSink",
    "LiveDiscordThreadManager",
    "ProjectionPublishResult",
    "SharedMeetingThreadProjectionPolicy",
    "TeamBotTopology",
    "ThreadCreateResult",
    "default_team_bot_topology",
]
