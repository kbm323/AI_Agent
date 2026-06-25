"""Phase 27 — Always-on Service Supervision Pilot tests.

Verifies that ServiceSupervisionPolicy defines and enforces the Gate 8
conditions for all 7 Hermes profiles without permission expansion.
No live processes are started — this is a policy/verification layer only.
"""

from __future__ import annotations

import pytest

from runtime_architecture_v2.projection import DiscordLiveBoundaryPolicy
from runtime_architecture_v2.service_supervision import (
    LogBound,
    RestartPolicy,
    ServiceProfile,
    ServiceSupervisionDecision,
    ServiceSupervisionPolicy,
    ServiceSupervisionStatus,
    _expected_profile_env_path,
)

# ── AC1: current_verified() returns 7 profiles ──────────────────────────


class TestCurrentVerifiedProfiles:
    """AC1: current_verified() returns exactly 7 profiles matching
    DiscordLiveBoundaryPolicy."""

    def test_returns_seven_profiles(self):
        policy = ServiceSupervisionPolicy.current_verified()
        assert len(policy.profiles) == 7

    def test_profile_names_match_boundary_policy(self):
        policy = ServiceSupervisionPolicy.current_verified()
        boundary = DiscordLiveBoundaryPolicy.current_verified()
        policy_names = {p.profile_name for p in policy.profiles}
        boundary_names = set(boundary.allowed_channel_ids_by_profile.keys())
        assert policy_names == boundary_names

    def test_expected_profile_names(self):
        policy = ServiceSupervisionPolicy.current_verified()
        expected = {
            "aicompanyassistant",
            "aicompanyceo",
            "aicompanycontent",
            "aicompanyart",
            "aicompanytech",
            "aicompanymarketing",
            "aicompanyquality",
        }
        actual = {p.profile_name for p in policy.profiles}
        assert actual == expected


# ── AC2: each profile has all required fields ────────────────────────────


class TestProfileFields:
    """AC2: each profile defines start/stop/status/heartbeat/log/restart/
    secrets."""

    @pytest.mark.parametrize(
        "field_name",
        [
            "start_command",
            "stop_command",
            "status_command",
            "heartbeat_interval_seconds",
            "log_bound",
            "restart_policy",
            "secrets_env_path",
        ],
    )
    def test_all_profiles_have_field(self, field_name):
        policy = ServiceSupervisionPolicy.current_verified()
        for profile in policy.profiles:
            value = getattr(profile, field_name)
            assert value is not None
            assert value != ""

    def test_start_command_is_hermes_profile(self):
        policy = ServiceSupervisionPolicy.current_verified()
        for profile in policy.profiles:
            assert "hermes" in profile.start_command
            assert profile.profile_name in profile.start_command

    def test_stop_command_is_terminate(self):
        policy = ServiceSupervisionPolicy.current_verified()
        for profile in policy.profiles:
            assert "TERM" in profile.stop_command or "stop" in profile.stop_command

    def test_status_command_checks_process(self):
        policy = ServiceSupervisionPolicy.current_verified()
        for profile in policy.profiles:
            assert (
                "status" in profile.status_command
                or "pgrep" in profile.status_command
            )


# ── AC3: evaluate() fails closed when conditions unmet ───────────────────


class TestEvaluateFailClosed:
    """AC3: evaluate() fails closed when any Gate 8 condition is unmet."""

    def test_fails_when_profiles_empty(self):
        policy = ServiceSupervisionPolicy(
            profiles=(),
            permission_mutation_allowed=False,
            administrator_allowed=False,
        )
        decision = policy.evaluate()
        assert decision.status == ServiceSupervisionStatus.FAIL
        assert "no_profiles" in decision.reason

    def test_fails_when_missing_heartbeat(self):
        policy = ServiceSupervisionPolicy.current_verified()
        bad_profiles = tuple(
            ServiceProfile(
                profile_name=p.profile_name,
                start_command=p.start_command,
                stop_command=p.stop_command,
                status_command=p.status_command,
                heartbeat_interval_seconds=0,
                log_bound=p.log_bound,
                restart_policy=p.restart_policy,
                secrets_env_path=p.secrets_env_path,
            )
            for p in policy.profiles
        )
        bad_policy = ServiceSupervisionPolicy(
            profiles=bad_profiles,
            permission_mutation_allowed=False,
            administrator_allowed=False,
        )
        decision = bad_policy.evaluate()
        assert decision.status == ServiceSupervisionStatus.FAIL
        assert "heartbeat" in decision.reason

    def test_fails_when_missing_log_bound(self):
        policy = ServiceSupervisionPolicy.current_verified()
        bad_profiles = tuple(
            ServiceProfile(
                profile_name=p.profile_name,
                start_command=p.start_command,
                stop_command=p.stop_command,
                status_command=p.status_command,
                heartbeat_interval_seconds=p.heartbeat_interval_seconds,
                log_bound=LogBound(max_size_mb=0, rotation_count=0),
                restart_policy=p.restart_policy,
                secrets_env_path=p.secrets_env_path,
            )
            for p in policy.profiles
        )
        bad_policy = ServiceSupervisionPolicy(
            profiles=bad_profiles,
            permission_mutation_allowed=False,
            administrator_allowed=False,
        )
        decision = bad_policy.evaluate()
        assert decision.status == ServiceSupervisionStatus.FAIL
        assert "log" in decision.reason

    def test_fails_when_missing_restart_policy(self):
        policy = ServiceSupervisionPolicy.current_verified()
        bad_profiles = tuple(
            ServiceProfile(
                profile_name=p.profile_name,
                start_command=p.start_command,
                stop_command=p.stop_command,
                status_command=p.status_command,
                heartbeat_interval_seconds=p.heartbeat_interval_seconds,
                log_bound=p.log_bound,
                restart_policy=RestartPolicy(
                    strategy="", max_restarts=0, backoff_seconds=0,
                ),
                secrets_env_path=p.secrets_env_path,
            )
            for p in policy.profiles
        )
        bad_policy = ServiceSupervisionPolicy(
            profiles=bad_profiles,
            permission_mutation_allowed=False,
            administrator_allowed=False,
        )
        decision = bad_policy.evaluate()
        assert decision.status == ServiceSupervisionStatus.FAIL
        assert "restart" in decision.reason

    def test_fails_when_missing_secrets_env(self):
        policy = ServiceSupervisionPolicy.current_verified()
        bad_profiles = tuple(
            ServiceProfile(
                profile_name=p.profile_name,
                start_command=p.start_command,
                stop_command=p.stop_command,
                status_command=p.status_command,
                heartbeat_interval_seconds=p.heartbeat_interval_seconds,
                log_bound=p.log_bound,
                restart_policy=p.restart_policy,
                secrets_env_path="",
            )
            for p in policy.profiles
        )
        bad_policy = ServiceSupervisionPolicy(
            profiles=bad_profiles,
            permission_mutation_allowed=False,
            administrator_allowed=False,
        )
        decision = bad_policy.evaluate()
        assert decision.status == ServiceSupervisionStatus.FAIL
        assert "secrets" in decision.reason

    def test_fails_when_missing_start_command(self):
        policy = ServiceSupervisionPolicy.current_verified()
        p = policy.profiles[0]
        bad_p = ServiceProfile(
            profile_name=p.profile_name,
            start_command="",
            stop_command=p.stop_command,
            status_command=p.status_command,
            heartbeat_interval_seconds=p.heartbeat_interval_seconds,
            log_bound=p.log_bound,
            restart_policy=p.restart_policy,
            secrets_env_path=p.secrets_env_path,
        )
        bad_policy = ServiceSupervisionPolicy(
            profiles=(bad_p,) + policy.profiles[1:],
            permission_mutation_allowed=False,
            administrator_allowed=False,
        )
        decision = bad_policy.evaluate()
        assert decision.status == ServiceSupervisionStatus.FAIL
        assert "start" in decision.reason


# ── AC4: evaluate() passes when all conditions met ───────────────────────


class TestEvaluatePass:
    """AC4: evaluate() passes when all 6 Gate 8 conditions are met."""

    def test_current_verified_passes(self):
        policy = ServiceSupervisionPolicy.current_verified()
        decision = policy.evaluate()
        assert decision.status == ServiceSupervisionStatus.PASS
        assert decision.reason == ""

    def test_pass_returns_seven_profile_count(self):
        policy = ServiceSupervisionPolicy.current_verified()
        decision = policy.evaluate()
        assert decision.profile_count == 7


# ── AC5: no permission expansion ─────────────────────────────────────────


class TestNoPermissionExpansion:
    """AC5: permission_mutation_allowed=False, administrator_allowed=False."""

    def test_permission_mutation_not_allowed(self):
        policy = ServiceSupervisionPolicy.current_verified()
        assert policy.permission_mutation_allowed is False

    def test_administrator_not_allowed(self):
        policy = ServiceSupervisionPolicy.current_verified()
        assert policy.administrator_allowed is False

    def test_fails_when_permission_mutation_allowed(self):
        policy = ServiceSupervisionPolicy.current_verified()
        bad = ServiceSupervisionPolicy(
            profiles=policy.profiles,
            permission_mutation_allowed=True,
            administrator_allowed=False,
        )
        decision = bad.evaluate()
        assert decision.status == ServiceSupervisionStatus.FAIL
        assert "permission" in decision.reason

    def test_fails_when_administrator_allowed(self):
        policy = ServiceSupervisionPolicy.current_verified()
        bad = ServiceSupervisionPolicy(
            profiles=policy.profiles,
            permission_mutation_allowed=False,
            administrator_allowed=True,
        )
        decision = bad.evaluate()
        assert decision.status == ServiceSupervisionStatus.FAIL
        assert "administrator" in decision.reason


# ── AC6: verification_report() ───────────────────────────────────────────


class TestVerificationReport:
    """AC6: verification_report() produces structured output."""

    def test_report_has_gate_name(self):
        policy = ServiceSupervisionPolicy.current_verified()
        report = policy.verification_report()
        assert "Gate 8" in report

    def test_report_has_profile_count(self):
        policy = ServiceSupervisionPolicy.current_verified()
        report = policy.verification_report()
        assert "7" in report

    def test_report_lists_all_profiles(self):
        policy = ServiceSupervisionPolicy.current_verified()
        report = policy.verification_report()
        for profile in policy.profiles:
            assert profile.profile_name in report


# ── AC7: heartbeat_interval > 0 ──────────────────────────────────────────


class TestHeartbeatInterval:
    """AC7: heartbeat_interval_seconds > 0 for all profiles."""

    def test_all_heartbeats_positive(self):
        policy = ServiceSupervisionPolicy.current_verified()
        for profile in policy.profiles:
            assert profile.heartbeat_interval_seconds > 0


# ── AC8: restart_policy has max_restarts and backoff ─────────────────────


class TestRestartPolicy:
    """AC8: restart_policy defines max_restarts and backoff."""

    def test_all_have_restart_strategy(self):
        policy = ServiceSupervisionPolicy.current_verified()
        for profile in policy.profiles:
            assert profile.restart_policy.strategy != ""
            assert profile.restart_policy.strategy in {"on-failure", "always", "no"}

    def test_all_have_max_restarts(self):
        policy = ServiceSupervisionPolicy.current_verified()
        for profile in policy.profiles:
            assert profile.restart_policy.max_restarts > 0

    def test_all_have_backoff_seconds(self):
        policy = ServiceSupervisionPolicy.current_verified()
        for profile in policy.profiles:
            assert profile.restart_policy.backoff_seconds > 0


# ── AC9: log_bound has max_size_mb and rotation_count ────────────────────


class TestLogBound:
    """AC9: log_bound defines max_size_mb and rotation_count."""

    def test_all_have_max_size(self):
        policy = ServiceSupervisionPolicy.current_verified()
        for profile in policy.profiles:
            assert profile.log_bound.max_size_mb > 0

    def test_all_have_rotation_count(self):
        policy = ServiceSupervisionPolicy.current_verified()
        for profile in policy.profiles:
            assert profile.log_bound.rotation_count > 0


# ── AC10: secrets_env_path is profile-local ──────────────────────────────


class TestSecretsEnvPath:
    """AC10: secrets_env_path is profile-local, never committed."""

    def test_all_have_secrets_env_path(self):
        policy = ServiceSupervisionPolicy.current_verified()
        for profile in policy.profiles:
            assert profile.secrets_env_path != ""
            assert profile.profile_name in profile.secrets_env_path

    def test_secrets_env_path_is_profile_local(self):
        policy = ServiceSupervisionPolicy.current_verified()
        for profile in policy.profiles:
            assert ".env" in profile.secrets_env_path
            assert profile.profile_name in profile.secrets_env_path


# ── AC11: profiles match DiscordLiveBoundaryPolicy exactly ──────────────


class TestBoundaryPolicyConsistency:
    """AC11: 7 profiles match DiscordLiveBoundaryPolicy keys exactly."""

    def test_exact_match_with_boundary_policy(self):
        policy = ServiceSupervisionPolicy.current_verified()
        boundary = DiscordLiveBoundaryPolicy.current_verified()
        policy_names = {p.profile_name for p in policy.profiles}
        boundary_names = set(boundary.allowed_channel_ids_by_profile.keys())
        assert policy_names == boundary_names

    def test_no_extra_profiles(self):
        policy = ServiceSupervisionPolicy.current_verified()
        boundary = DiscordLiveBoundaryPolicy.current_verified()
        boundary_names = set(boundary.allowed_channel_ids_by_profile.keys())
        for profile in policy.profiles:
            assert profile.profile_name in boundary_names


# ── Integrated smoke: all conditions together ────────────────────────────


class TestIntegratedSmoke:
    """Integrated smoke test: all Gate 8 conditions evaluated together."""

    def test_full_policy_passes_all_gates(self):
        policy = ServiceSupervisionPolicy.current_verified()
        decision = policy.evaluate()
        assert decision.status == ServiceSupervisionStatus.PASS
        assert decision.profile_count == 7
        assert decision.reason == ""
        assert decision.gate == "Gate 8"

    def test_decision_is_frozen_dataclass(self):
        policy = ServiceSupervisionPolicy.current_verified()
        decision = policy.evaluate()
        assert isinstance(decision, ServiceSupervisionDecision)
        assert decision.status == ServiceSupervisionStatus.PASS


# ── Review hardening: edge cases from independent review ────────────────


class TestReviewHardening:
    """Edge cases identified by independent review — all must fail-closed."""

    def test_fails_on_wrong_profile_count(self):
        """Condition 1: profile count must be exactly 7."""
        policy = ServiceSupervisionPolicy.current_verified()
        # Only 1 profile
        bad = ServiceSupervisionPolicy(
            profiles=policy.profiles[:1],
        )
        decision = bad.evaluate()
        assert decision.status == ServiceSupervisionStatus.FAIL
        assert "profile_count_mismatch" in decision.reason

    def test_fails_on_duplicate_profiles(self):
        """No duplicate profiles allowed."""
        policy = ServiceSupervisionPolicy.current_verified()
        p0 = policy.profiles[0]
        bad = ServiceSupervisionPolicy(
            profiles=(p0, p0, p0, p0, p0, p0, p0),
        )
        decision = bad.evaluate()
        assert decision.status == ServiceSupervisionStatus.FAIL
        assert "duplicate" in decision.reason

    def test_fails_on_impostor_profile_name(self):
        """Profile names must match expected set exactly."""
        policy = ServiceSupervisionPolicy.current_verified()
        good = list(policy.profiles)
        # Replace first profile with an impostor
        good[0] = ServiceProfile(
            profile_name="IMPOSTOR",
            start_command="hermes --profile IMPOSTOR --discord",
            stop_command="kill -TERM",
            status_command="pgrep -f impostor",
            heartbeat_interval_seconds=60,
            log_bound=good[0].log_bound,
            restart_policy=good[0].restart_policy,
            secrets_env_path="~/.hermes/profiles/IMPOSTOR/.env",
        )
        bad = ServiceSupervisionPolicy(profiles=tuple(good))
        decision = bad.evaluate()
        assert decision.status == ServiceSupervisionStatus.FAIL
        assert "profile_name_mismatch" in decision.reason

    def test_fails_on_empty_log_dir(self):
        """log_dir must be non-empty."""
        policy = ServiceSupervisionPolicy.current_verified()
        p = policy.profiles[0]
        bad_p = ServiceProfile(
            profile_name=p.profile_name,
            start_command=p.start_command,
            stop_command=p.stop_command,
            status_command=p.status_command,
            heartbeat_interval_seconds=p.heartbeat_interval_seconds,
            log_bound=LogBound(
                max_size_mb=50, rotation_count=5, log_dir="",
            ),
            restart_policy=p.restart_policy,
            secrets_env_path=p.secrets_env_path,
        )
        bad = ServiceSupervisionPolicy(profiles=(bad_p,) + policy.profiles[1:])
        decision = bad.evaluate()
        assert decision.status == ServiceSupervisionStatus.FAIL
        assert "log_dir_missing" in decision.reason

    def test_fails_on_invalid_restart_strategy(self):
        """Restart strategy must be whitelisted."""
        policy = ServiceSupervisionPolicy.current_verified()
        p = policy.profiles[0]
        bad_p = ServiceProfile(
            profile_name=p.profile_name,
            start_command=p.start_command,
            stop_command=p.stop_command,
            status_command=p.status_command,
            heartbeat_interval_seconds=p.heartbeat_interval_seconds,
            log_bound=p.log_bound,
            restart_policy=RestartPolicy(
                strategy="invalid_garbage",
                max_restarts=3,
                backoff_seconds=30,
            ),
            secrets_env_path=p.secrets_env_path,
        )
        bad = ServiceSupervisionPolicy(profiles=(bad_p,) + policy.profiles[1:])
        decision = bad.evaluate()
        assert decision.status == ServiceSupervisionStatus.FAIL
        assert "restart_policy_invalid" in decision.reason

    def test_fails_on_non_profile_local_secrets_path(self):
        """secrets_env_path must contain profile name and .env."""
        policy = ServiceSupervisionPolicy.current_verified()
        p = policy.profiles[0]
        bad_p = ServiceProfile(
            profile_name=p.profile_name,
            start_command=p.start_command,
            stop_command=p.stop_command,
            status_command=p.status_command,
            heartbeat_interval_seconds=p.heartbeat_interval_seconds,
            log_bound=p.log_bound,
            restart_policy=p.restart_policy,
            secrets_env_path="/etc/environment",
        )
        bad = ServiceSupervisionPolicy(profiles=(bad_p,) + policy.profiles[1:])
        decision = bad.evaluate()
        assert decision.status == ServiceSupervisionStatus.FAIL
        assert "not_profile_local" in decision.reason

    def test_fails_on_missing_stop_command(self):
        """stop_command must be non-empty."""
        policy = ServiceSupervisionPolicy.current_verified()
        p = policy.profiles[0]
        bad_p = ServiceProfile(
            profile_name=p.profile_name,
            start_command=p.start_command,
            stop_command="",
            status_command=p.status_command,
            heartbeat_interval_seconds=p.heartbeat_interval_seconds,
            log_bound=p.log_bound,
            restart_policy=p.restart_policy,
            secrets_env_path=p.secrets_env_path,
        )
        bad = ServiceSupervisionPolicy(profiles=(bad_p,) + policy.profiles[1:])
        decision = bad.evaluate()
        assert decision.status == ServiceSupervisionStatus.FAIL
        assert "stop_command_missing" in decision.reason

    def test_fails_on_missing_status_command(self):
        """status_command must be non-empty."""
        policy = ServiceSupervisionPolicy.current_verified()
        p = policy.profiles[0]
        bad_p = ServiceProfile(
            profile_name=p.profile_name,
            start_command=p.start_command,
            stop_command=p.stop_command,
            status_command="",
            heartbeat_interval_seconds=p.heartbeat_interval_seconds,
            log_bound=p.log_bound,
            restart_policy=p.restart_policy,
            secrets_env_path=p.secrets_env_path,
        )
        bad = ServiceSupervisionPolicy(profiles=(bad_p,) + policy.profiles[1:])
        decision = bad.evaluate()
        assert decision.status == ServiceSupervisionStatus.FAIL
        assert "status_command_missing" in decision.reason

    def test_fails_on_tmp_env_path_even_when_profile_name_present(self):
        """secrets_env_path must be exact profile-local Hermes env."""
        policy = ServiceSupervisionPolicy.current_verified()
        p = policy.profiles[0]
        bad_p = ServiceProfile(
            profile_name=p.profile_name,
            start_command=p.start_command,
            stop_command=p.stop_command,
            status_command=p.status_command,
            heartbeat_interval_seconds=p.heartbeat_interval_seconds,
            log_bound=p.log_bound,
            restart_policy=p.restart_policy,
            secrets_env_path=f"/tmp/{p.profile_name}.env",
        )
        bad = ServiceSupervisionPolicy(profiles=(bad_p,) + policy.profiles[1:])
        decision = bad.evaluate()
        assert decision.status == ServiceSupervisionStatus.FAIL
        assert "secrets_env_path_not_profile_local" in decision.reason

    def test_fails_on_repo_local_env_path(self):
        """repo-local env files are not profile-local Hermes env files."""
        policy = ServiceSupervisionPolicy.current_verified()
        p = policy.profiles[0]
        bad_p = ServiceProfile(
            profile_name=p.profile_name,
            start_command=p.start_command,
            stop_command=p.stop_command,
            status_command=p.status_command,
            heartbeat_interval_seconds=p.heartbeat_interval_seconds,
            log_bound=p.log_bound,
            restart_policy=p.restart_policy,
            secrets_env_path=f"./profiles/{p.profile_name}/.env",
        )
        bad = ServiceSupervisionPolicy(profiles=(bad_p,) + policy.profiles[1:])
        decision = bad.evaluate()
        assert decision.status == ServiceSupervisionStatus.FAIL
        assert "secrets_env_path_not_profile_local" in decision.reason

    def test_expected_profile_env_path_shape(self):
        assert _expected_profile_env_path("aicompanyassistant") == (
            "~/.hermes/profiles/aicompanyassistant/.env"
        )

    def test_evaluate_fails_when_boundary_policy_profiles_drift(self):
        policy = ServiceSupervisionPolicy.current_verified()
        boundary = DiscordLiveBoundaryPolicy.current_verified()
        drifted = DiscordLiveBoundaryPolicy(
            guild_id=boundary.guild_id,
            allowed_channel_ids_by_profile={
                k: v for k, v in boundary.allowed_channel_ids_by_profile.items()
                if k != "aicompanyassistant"
            },
            permission_mutation_allowed=boundary.permission_mutation_allowed,
            administrator_allowed=boundary.administrator_allowed,
            require_mention=boundary.require_mention,
            thread_require_mention=boundary.thread_require_mention,
            free_response_channels=boundary.free_response_channels,
        )
        decision = policy.evaluate(discord_boundary_policy=drifted)
        assert decision.status == ServiceSupervisionStatus.FAIL
        assert "discord_boundary_profile_mismatch" in decision.reason

    def test_evaluate_fails_when_boundary_mentions_disabled(self):
        policy = ServiceSupervisionPolicy.current_verified()
        boundary = DiscordLiveBoundaryPolicy.current_verified()
        unsafe = DiscordLiveBoundaryPolicy(
            guild_id=boundary.guild_id,
            allowed_channel_ids_by_profile=boundary.allowed_channel_ids_by_profile,
            permission_mutation_allowed=boundary.permission_mutation_allowed,
            administrator_allowed=boundary.administrator_allowed,
            require_mention=False,
            thread_require_mention=boundary.thread_require_mention,
            free_response_channels=boundary.free_response_channels,
        )
        decision = policy.evaluate(discord_boundary_policy=unsafe)
        assert decision.status == ServiceSupervisionStatus.FAIL
        assert "discord_boundary_mention_gate_required" in decision.reason

    def test_evaluate_fails_when_boundary_thread_mentions_disabled(self):
        policy = ServiceSupervisionPolicy.current_verified()
        boundary = DiscordLiveBoundaryPolicy.current_verified()
        unsafe = DiscordLiveBoundaryPolicy(
            guild_id=boundary.guild_id,
            allowed_channel_ids_by_profile=boundary.allowed_channel_ids_by_profile,
            permission_mutation_allowed=boundary.permission_mutation_allowed,
            administrator_allowed=boundary.administrator_allowed,
            require_mention=boundary.require_mention,
            thread_require_mention=False,
            free_response_channels=boundary.free_response_channels,
        )
        decision = policy.evaluate(discord_boundary_policy=unsafe)
        assert decision.status == ServiceSupervisionStatus.FAIL
        assert "discord_boundary_mention_gate_required" in decision.reason
