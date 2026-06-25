"""Phase 27 always-on service supervision policy.

Machine-checkable supervision boundary for Gate 8. This module does not
start, stop, or monitor live processes. It defines the required supervision
bounds for each of the 7 live Hermes profiles and verifies that all six
Gate 8 conditions are documented and consistent with the existing
DiscordLiveBoundaryPolicy — without any permission expansion.

Gate 8 conditions:
    1. one gateway/service process per live bot profile
    2. status/start/stop scripts documented
    3. logs rotate or are bounded
    4. process restart policy exists
    5. health endpoint or periodic heartbeat exists
    6. secrets loaded from profile-local env, never committed
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum, unique


@unique
class ServiceSupervisionStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"


@dataclass(frozen=True)
class LogBound:
    """Log rotation/bounding configuration for one profile."""

    max_size_mb: int
    rotation_count: int
    log_dir: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "max_size_mb": self.max_size_mb,
            "rotation_count": self.rotation_count,
            "log_dir": self.log_dir,
        }


@dataclass(frozen=True)
class RestartPolicy:
    """Process restart policy for one profile."""

    strategy: str
    max_restarts: int
    backoff_seconds: int

    def to_dict(self) -> dict[str, object]:
        return {
            "strategy": self.strategy,
            "max_restarts": self.max_restarts,
            "backoff_seconds": self.backoff_seconds,
        }


@dataclass(frozen=True)
class ServiceProfile:
    """Supervision bounds for one live Hermes bot profile.

    All fields are documentation/bound declarations — no live execution
    is performed by this module.
    """

    profile_name: str
    start_command: str
    stop_command: str
    status_command: str
    heartbeat_interval_seconds: int
    log_bound: LogBound
    restart_policy: RestartPolicy
    secrets_env_path: str

    def to_dict(self) -> dict[str, object]:
        return {
            "profile_name": self.profile_name,
            "start_command": self.start_command,
            "stop_command": self.stop_command,
            "status_command": self.status_command,
            "heartbeat_interval_seconds": self.heartbeat_interval_seconds,
            "log_bound": self.log_bound.to_dict(),
            "restart_policy": self.restart_policy.to_dict(),
            "secrets_env_path": self.secrets_env_path,
        }


@dataclass(frozen=True)
class ServiceSupervisionDecision:
    """Result of evaluating all Gate 8 conditions."""

    status: ServiceSupervisionStatus
    reason: str
    gate: str = "Gate 8"
    profile_count: int = 0
    conditions_checked: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "reason": self.reason,
            "gate": self.gate,
            "profile_count": self.profile_count,
            "conditions_checked": list(self.conditions_checked),
        }


_GATE8_CONDITIONS = (
    "one_process_per_profile",
    "start_stop_status_documented",
    "logs_bounded",
    "restart_policy_exists",
    "heartbeat_exists",
    "secrets_from_profile_env",
)

_VALID_RESTART_STRATEGIES = frozenset({"on-failure", "always", "no"})

_EXPECTED_PROFILE_NAMES = frozenset({
    "aicompanyassistant",
    "aicompanyceo",
    "aicompanycontent",
    "aicompanyart",
    "aicompanytech",
    "aicompanymarketing",
    "aicompanyquality",
})


def _default_profiles() -> tuple[ServiceProfile, ...]:
    """Return the 7 verified live Hermes profiles with supervision bounds."""

    log_dir_template = "runtime/logs/{profile}"
    secrets_template = "~/.hermes/profiles/{profile}/.env"
    default_log = LogBound(max_size_mb=50, rotation_count=5)
    default_restart = RestartPolicy(
        strategy="on-failure", max_restarts=3, backoff_seconds=30,
    )

    names = (
        "aicompanyassistant",
        "aicompanyceo",
        "aicompanycontent",
        "aicompanyart",
        "aicompanytech",
        "aicompanymarketing",
        "aicompanyquality",
    )
    profiles: list[ServiceProfile] = []
    for name in names:
        profiles.append(
            ServiceProfile(
                profile_name=name,
                start_command=f"hermes --profile {name} --discord",
                stop_command=f"kill -TERM $(pgrep -f 'hermes --profile {name}')",
                status_command=(
                    f"pgrep -f 'hermes --profile {name}' "
                    "&& echo running || echo stopped"
                ),
                heartbeat_interval_seconds=60,
                log_bound=LogBound(
                    max_size_mb=default_log.max_size_mb,
                    rotation_count=default_log.rotation_count,
                    log_dir=log_dir_template.format(profile=name),
                ),
                restart_policy=RestartPolicy(
                    strategy=default_restart.strategy,
                    max_restarts=default_restart.max_restarts,
                    backoff_seconds=default_restart.backoff_seconds,
                ),
                secrets_env_path=secrets_template.format(profile=name),
            ),
        )
    return tuple(profiles)


@dataclass(frozen=True)
class ServiceSupervisionPolicy:
    """Policy model for Gate 8 — always-on service supervision.

    Defines and verifies the supervision bounds for all 7 live Hermes
    profiles.  No live process management is performed.  The policy is
    fail-closed: if any Gate 8 condition is unmet for any profile,
    evaluate() returns FAIL.
    """

    profiles: tuple[ServiceProfile, ...] = field(default_factory=tuple)
    permission_mutation_allowed: bool = False
    administrator_allowed: bool = False

    @classmethod
    def current_verified(cls) -> ServiceSupervisionPolicy:
        """Return the current verified 7-profile supervision policy."""
        return cls(
            profiles=_default_profiles(),
            permission_mutation_allowed=False,
            administrator_allowed=False,
        )

    def evaluate(self) -> ServiceSupervisionDecision:
        """Evaluate all Gate 8 conditions. Fail-closed on any unmet condition."""

        if not self.profiles:
            return ServiceSupervisionDecision(
                status=ServiceSupervisionStatus.FAIL,
                reason="no_profiles: no service profiles defined",
                profile_count=0,
                conditions_checked=_GATE8_CONDITIONS,
            )

        # Condition 1: one process per profile — exactly 7 expected profiles
        profile_names = [p.profile_name for p in self.profiles]
        name_set = set(profile_names)

        if len(self.profiles) != len(_EXPECTED_PROFILE_NAMES):
            return ServiceSupervisionDecision(
                status=ServiceSupervisionStatus.FAIL,
                reason=(
                    f"profile_count_mismatch: expected "
                    f"{len(_EXPECTED_PROFILE_NAMES)}, got "
                    f"{len(self.profiles)}"
                ),
                profile_count=len(self.profiles),
                conditions_checked=_GATE8_CONDITIONS,
            )

        # No duplicate profiles
        if len(name_set) != len(profile_names):
            duplicates = [
                n for n in profile_names if profile_names.count(n) > 1
            ]
            return ServiceSupervisionDecision(
                status=ServiceSupervisionStatus.FAIL,
                reason=(
                    f"duplicate_profiles: {set(duplicates)} "
                    "— each profile must appear exactly once"
                ),
                profile_count=len(self.profiles),
                conditions_checked=_GATE8_CONDITIONS,
            )

        # Profile names must match expected set exactly
        if name_set != _EXPECTED_PROFILE_NAMES:
            unexpected = name_set - _EXPECTED_PROFILE_NAMES
            missing = _EXPECTED_PROFILE_NAMES - name_set
            parts: list[str] = []
            if unexpected:
                parts.append(f"unexpected: {unexpected}")
            if missing:
                parts.append(f"missing: {missing}")
            return ServiceSupervisionDecision(
                status=ServiceSupervisionStatus.FAIL,
                reason=(
                    f"profile_name_mismatch: {', '.join(parts)}"
                ),
                profile_count=len(self.profiles),
                conditions_checked=_GATE8_CONDITIONS,
            )

        if self.permission_mutation_allowed:
            return ServiceSupervisionDecision(
                status=ServiceSupervisionStatus.FAIL,
                reason="permission_mutation_not_allowed: Gate 8 forbids "
                       "permission expansion",
                profile_count=len(self.profiles),
                conditions_checked=_GATE8_CONDITIONS,
            )

        if self.administrator_allowed:
            return ServiceSupervisionDecision(
                status=ServiceSupervisionStatus.FAIL,
                reason="administrator_not_allowed: Gate 8 forbids "
                       "Administrator privilege",
                profile_count=len(self.profiles),
                conditions_checked=_GATE8_CONDITIONS,
            )

        for profile in self.profiles:
            # Condition 2: start/stop/status documented
            if not profile.start_command:
                return ServiceSupervisionDecision(
                    status=ServiceSupervisionStatus.FAIL,
                    reason=f"start_command_missing for "
                           f"{profile.profile_name}",
                    profile_count=len(self.profiles),
                    conditions_checked=_GATE8_CONDITIONS,
                )
            if not profile.stop_command:
                return ServiceSupervisionDecision(
                    status=ServiceSupervisionStatus.FAIL,
                    reason=f"stop_command_missing for "
                           f"{profile.profile_name}",
                    profile_count=len(self.profiles),
                    conditions_checked=_GATE8_CONDITIONS,
                )
            if not profile.status_command:
                return ServiceSupervisionDecision(
                    status=ServiceSupervisionStatus.FAIL,
                    reason=f"status_command_missing for "
                           f"{profile.profile_name}",
                    profile_count=len(self.profiles),
                    conditions_checked=_GATE8_CONDITIONS,
                )

            # Condition 5: heartbeat exists
            if profile.heartbeat_interval_seconds <= 0:
                return ServiceSupervisionDecision(
                    status=ServiceSupervisionStatus.FAIL,
                    reason=f"heartbeat_invalid for {profile.profile_name}: "
                           "interval must be > 0",
                    profile_count=len(self.profiles),
                    conditions_checked=_GATE8_CONDITIONS,
                )

            # Condition 3: logs bounded — size, rotation, and dir
            if (
                profile.log_bound.max_size_mb <= 0
                or profile.log_bound.rotation_count <= 0
            ):
                return ServiceSupervisionDecision(
                    status=ServiceSupervisionStatus.FAIL,
                    reason=f"log_bound_invalid for {profile.profile_name}: "
                           "max_size_mb and rotation_count must be > 0",
                    profile_count=len(self.profiles),
                    conditions_checked=_GATE8_CONDITIONS,
                )
            if not profile.log_bound.log_dir:
                return ServiceSupervisionDecision(
                    status=ServiceSupervisionStatus.FAIL,
                    reason=f"log_dir_missing for {profile.profile_name}: "
                           "log directory must be non-empty",
                    profile_count=len(self.profiles),
                    conditions_checked=_GATE8_CONDITIONS,
                )

            # Condition 4: restart policy exists — strategy must be whitelisted
            if (
                profile.restart_policy.strategy
                not in _VALID_RESTART_STRATEGIES
                or profile.restart_policy.max_restarts <= 0
                or profile.restart_policy.backoff_seconds <= 0
            ):
                return ServiceSupervisionDecision(
                    status=ServiceSupervisionStatus.FAIL,
                    reason=f"restart_policy_invalid for "
                           f"{profile.profile_name}: strategy must be one "
                           f"of {sorted(_VALID_RESTART_STRATEGIES)}, "
                           "max_restarts and backoff_seconds must be > 0",
                    profile_count=len(self.profiles),
                    conditions_checked=_GATE8_CONDITIONS,
                )

            # Condition 6: secrets from profile-local env
            # Path must be non-empty, contain .env, and contain profile name
            if not profile.secrets_env_path:
                return ServiceSupervisionDecision(
                    status=ServiceSupervisionStatus.FAIL,
                    reason=f"secrets_env_path_missing for "
                           f"{profile.profile_name}",
                    profile_count=len(self.profiles),
                    conditions_checked=_GATE8_CONDITIONS,
                )
            if (
                ".env" not in profile.secrets_env_path
                or profile.profile_name not in profile.secrets_env_path
            ):
                return ServiceSupervisionDecision(
                    status=ServiceSupervisionStatus.FAIL,
                    reason=(
                        f"secrets_env_path_not_profile_local for "
                        f"{profile.profile_name}: path must contain .env "
                        "and the profile name"
                    ),
                    profile_count=len(self.profiles),
                    conditions_checked=_GATE8_CONDITIONS,
                )

        return ServiceSupervisionDecision(
            status=ServiceSupervisionStatus.PASS,
            reason="",
            profile_count=len(self.profiles),
            conditions_checked=_GATE8_CONDITIONS,
        )

    def verification_report(self) -> str:
        """Produce a human-readable verification report."""

        decision = self.evaluate()
        lines = [
            "Gate 8 — Service Supervision Verification",
            f"  Status: {decision.status.value}",
            f"  Profiles: {decision.profile_count}",
            f"  Conditions checked: {', '.join(decision.conditions_checked)}",
        ]
        if decision.reason:
            lines.append(f"  Reason: {decision.reason}")
        lines.append("")
        lines.append("  Profile supervision bounds:")
        for profile in self.profiles:
            lines.append(f"    [{profile.profile_name}]")
            lines.append(f"      start: {profile.start_command}")
            lines.append(f"      stop: {profile.stop_command}")
            lines.append(f"      status: {profile.status_command}")
            lines.append(
                f"      heartbeat: every {profile.heartbeat_interval_seconds}s"
            )
            lines.append(
                f"      log: max {profile.log_bound.max_size_mb}MB, "
                f"{profile.log_bound.rotation_count} rotations, "
                f"dir={profile.log_bound.log_dir}"
            )
            lines.append(
                f"      restart: {profile.restart_policy.strategy}, "
                f"max {profile.restart_policy.max_restarts}, "
                f"backoff {profile.restart_policy.backoff_seconds}s"
            )
            lines.append(f"      secrets: {profile.secrets_env_path}")
        lines.append("")
        lines.append(
            "  Permission mutation: "
            f"{'allowed' if self.permission_mutation_allowed else 'forbidden'}"
        )
        lines.append(
            "  Administrator: "
            f"{'allowed' if self.administrator_allowed else 'forbidden'}"
        )
        return "\n".join(lines)
