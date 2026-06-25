"""Phase 28 — Full Live Closed-loop Pilot tests.

The pilot verifies a controlled Hermes Gateway → MeetingRun → worker →
validation → projection loop. It must remain Hermes-first and fail-closed;
unit tests use fake/injected boundaries only and never perform real Discord
or real worker CLI calls.
"""

from __future__ import annotations

from dataclasses import replace

from runtime_architecture_v2.closed_loop_pilot import (
    ClosedLoopPilotPolicy,
    ClosedLoopStatus,
    GatewayInput,
    ProjectionSafetyPolicy,
    run_phase28_closed_loop_pilot,
)
from runtime_architecture_v2.command_surface import (
    CommandSurfaceMode,
    HermesGatewayCommandSurfacePolicy,
)
from runtime_architecture_v2.projection import DiscordLiveBoundaryPolicy
from runtime_architecture_v2.service_supervision import (
    LogBound,
    ServiceProfile,
    ServiceSupervisionPolicy,
)
from runtime_architecture_v2.worker_boundary_smoke import (
    BoundarySmokeCheck,
    LiveWorkerBoundarySmokePolicy,
)


class TestProjectionSafetyPolicy:
    """Gate 9 projection safety policy."""

    def test_current_verified_passes(self):
        policy = ProjectionSafetyPolicy.current_verified()
        decision = policy.evaluate(
            content="trace_id=trace-123\nHello @everyone token=supersecret",
            trace_id="trace-123",
            raw_worker_outputs=("raw secret output",),
        )
        assert decision.status == ClosedLoopStatus.PASS
        assert "@everyone" not in decision.safe_content
        assert "supersecret" not in decision.safe_content
        assert "raw secret output" not in decision.safe_content
        assert "raw_worker_outputs: omitted" in decision.safe_content
        assert "trace-123" in decision.safe_content

    def test_fails_when_trace_id_missing(self):
        policy = ProjectionSafetyPolicy.current_verified()
        decision = policy.evaluate(
            content="safe content",
            trace_id="",
            raw_worker_outputs=(),
        )
        assert decision.status == ClosedLoopStatus.FAIL
        assert "trace" in decision.reason

    def test_fails_when_content_empty_after_sanitization(self):
        policy = ProjectionSafetyPolicy.current_verified()
        decision = policy.evaluate(
            content="",
            trace_id="trace-123",
            raw_worker_outputs=(),
        )
        assert decision.status == ClosedLoopStatus.FAIL
        assert "content" in decision.reason

    def test_caps_content_length(self):
        policy = ProjectionSafetyPolicy.current_verified(max_content_length=80)
        decision = policy.evaluate(
            content="trace_id=trace-123\n" + ("x" * 500),
            trace_id="trace-123",
            raw_worker_outputs=(),
        )
        assert decision.status == ClosedLoopStatus.PASS
        assert len(decision.safe_content) <= 80
        assert "trace-123" in decision.safe_content

    def test_fails_when_cap_would_drop_trace_id(self):
        policy = ProjectionSafetyPolicy.current_verified(max_content_length=8)
        decision = policy.evaluate(
            content="safe body",
            trace_id="trace-123456",
            raw_worker_outputs=(),
        )
        assert decision.status == ClosedLoopStatus.FAIL
        assert decision.reason == "trace_id_not_preserved_after_content_cap"

    def test_fails_when_max_content_length_invalid(self):
        policy = ProjectionSafetyPolicy.current_verified(max_content_length=0)
        decision = policy.evaluate(
            content="trace_id=trace-123 safe",
            trace_id="trace-123",
            raw_worker_outputs=(),
        )
        assert decision.status == ClosedLoopStatus.FAIL
        assert decision.reason == "content_length_cap_invalid"

    def test_fails_when_required_safety_flags_disabled(self):
        for policy in (
            ProjectionSafetyPolicy(allowed_mentions_constrained=False),
            ProjectionSafetyPolicy(break_mass_mentions=False),
            ProjectionSafetyPolicy(omit_raw_worker_outputs=False),
            ProjectionSafetyPolicy(redact_secret_like_values=False),
        ):
            decision = policy.evaluate(
                content="trace_id=trace-123 safe",
                trace_id="trace-123",
                raw_worker_outputs=(),
            )
            assert decision.status == ClosedLoopStatus.FAIL


class TestClosedLoopPilotPolicy:
    """Phase 28 policy composition."""

    def test_current_verified_composes_prior_phase_guards(self):
        policy = ClosedLoopPilotPolicy.current_verified()
        assert isinstance(
            policy.command_surface_policy,
            HermesGatewayCommandSurfacePolicy,
        )
        assert isinstance(policy.discord_boundary_policy, DiscordLiveBoundaryPolicy)
        assert isinstance(policy.worker_boundary_policy, LiveWorkerBoundarySmokePolicy)
        assert isinstance(policy.service_supervision_policy, ServiceSupervisionPolicy)
        assert isinstance(policy.projection_safety_policy, ProjectionSafetyPolicy)

    def test_current_verified_passes(self):
        policy = ClosedLoopPilotPolicy.current_verified()
        decision = policy.evaluate()
        assert decision.status == ClosedLoopStatus.PASS
        assert decision.reason == ""

    def test_fails_when_interaction_endpoint_enabled(self):
        base_surface = HermesGatewayCommandSurfacePolicy.current_verified()
        policy = ClosedLoopPilotPolicy.current_verified(
            command_surface_policy=replace(
                base_surface,
                standalone_slash_adapter_enabled=False,
                interaction_endpoint_enabled=True,
                permission_mutation_allowed=False,
                administrator_allowed=False,
            )
        )
        decision = policy.evaluate()
        assert decision.status == ClosedLoopStatus.FAIL
        assert "command_surface" in decision.reason

    def test_fails_when_worker_boundary_fails(self):
        base_worker = LiveWorkerBoundarySmokePolicy.current_verified()
        worker_policy = LiveWorkerBoundarySmokePolicy(
            checks=tuple(
                BoundarySmokeCheck(
                    name=check.name,
                    description=check.description,
                    passed=False if check.name == "output_sanitized" else check.passed,
                )
                for check in base_worker.checks
            )
        )
        policy = ClosedLoopPilotPolicy.current_verified(
            worker_boundary_policy=worker_policy,
        )
        decision = policy.evaluate()
        assert decision.status == ClosedLoopStatus.FAIL
        assert "worker_boundary" in decision.reason

    def test_fails_when_service_supervision_fails(self):
        service = ServiceSupervisionPolicy.current_verified()
        p = service.profiles[0]
        bad_profile = ServiceProfile(
            profile_name=p.profile_name,
            start_command=p.start_command,
            stop_command=p.stop_command,
            status_command=p.status_command,
            heartbeat_interval_seconds=p.heartbeat_interval_seconds,
            log_bound=LogBound(max_size_mb=0, rotation_count=0, log_dir=""),
            restart_policy=p.restart_policy,
            secrets_env_path=p.secrets_env_path,
        )
        bad_service = ServiceSupervisionPolicy(
            profiles=(bad_profile,) + service.profiles[1:],
        )
        policy = ClosedLoopPilotPolicy.current_verified(
            service_supervision_policy=bad_service,
        )
        decision = policy.evaluate()
        assert decision.status == ClosedLoopStatus.FAIL
        assert "service_supervision" in decision.reason


class TestRunPhase28ClosedLoopPilot:
    """Controlled closed-loop pilot runner."""

    def test_controlled_dry_run_completes_full_stage_sequence(self, tmp_path):
        result = run_phase28_closed_loop_pilot(root=tmp_path)

        assert result.ok is True
        assert result.status == ClosedLoopStatus.PASS
        assert result.mode == "controlled-dry-run"
        assert result.meeting_run_id.startswith("mr-phase28-")
        assert result.projection_status == "published"
        assert result.final_state == "completed"
        assert result.live_discord_attempted is False
        assert result.stage_sequence == (
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

    def test_controlled_dry_run_preserves_trace_id(self, tmp_path):
        result = run_phase28_closed_loop_pilot(
            root=tmp_path,
            gateway_input=GatewayInput(
                trigger_text="회의 요청",
                user_id="u1",
                channel_id="phase28-channel",
                guild_id="1505600166676271244",
                trace_id="trace-phase28-custom",
                surface=CommandSurfaceMode.HERMES_EXISTING_GATEWAY,
            ),
        )
        assert result.trace_id == "trace-phase28-custom"
        assert "trace-phase28-custom" in result.projection_content

    def test_projection_sanitizes_mentions_secrets_and_raw_worker_output(
        self,
        tmp_path,
    ):
        result = run_phase28_closed_loop_pilot(
            root=tmp_path,
            raw_worker_outputs=(
                "worker says @everyone bearer abcdef and token=supersecret",
            ),
        )
        assert result.ok is True
        assert "@everyone" not in result.projection_content
        assert "supersecret" not in result.projection_content
        assert "abcdef" not in result.projection_content
        assert "worker says" not in result.projection_content
        assert "raw_worker_outputs: omitted" in result.projection_content

    def test_writes_artifact_with_trace_and_stages(self, tmp_path):
        result = run_phase28_closed_loop_pilot(root=tmp_path)
        artifact = result.artifact_path
        assert artifact.endswith("phase28_closed_loop_report.json")
        data = (tmp_path / artifact).read_text(encoding="utf-8")
        assert result.trace_id in data
        assert "gateway_input_received" in data
        assert "projection_published" in data

    def test_fails_closed_when_policy_fails(self, tmp_path):
        base_surface = HermesGatewayCommandSurfacePolicy.current_verified()
        policy = ClosedLoopPilotPolicy.current_verified(
            command_surface_policy=replace(
                base_surface,
                interaction_endpoint_enabled=True,
            )
        )
        result = run_phase28_closed_loop_pilot(root=tmp_path, policy=policy)
        assert result.ok is False
        assert result.status == ClosedLoopStatus.FAIL
        assert "command_surface" in result.error
        assert result.projection_status == "not_attempted"

    def test_fails_closed_when_live_projection_requested_without_injected_http(
        self,
        tmp_path,
    ):
        result = run_phase28_closed_loop_pilot(
            root=tmp_path,
            controlled_live_projection=True,
            discord_http_post=None,
        )
        assert result.ok is False
        assert result.status == ClosedLoopStatus.FAIL
        assert result.error == "live_projection_requires_injected_http"
        assert result.live_discord_attempted is False

    def test_controlled_live_projection_uses_injected_http_and_allowed_mentions(
        self,
        tmp_path,
    ):
        calls = []

        def fake_http_post(url, *, headers, json_body, timeout_seconds):
            calls.append({
                "url": url,
                "headers": headers,
                "json_body": json_body,
                "timeout_seconds": timeout_seconds,
            })
            return {"status_code": 200, "json": {"id": "msg-phase28"}}

        result = run_phase28_closed_loop_pilot(
            root=tmp_path,
            controlled_live_projection=True,
            env={"DISCORD_BOT_TOKEN": "fake-token-for-test"},
            discord_http_post=fake_http_post,
            target_profile="aicompanyassistant",
            target_channel_id="home:aicompanyassistant:#일일-브리핑",
        )

        assert result.ok is True
        assert result.live_discord_attempted is True
        assert result.projection_status == "published"
        assert result.discord_message_id == "msg-phase28"
        assert calls
        assert calls[0]["json_body"]["allowed_mentions"] == {"parse": []}

    def test_controlled_live_projection_fail_makes_top_level_not_ok(
        self,
        tmp_path,
    ):
        def fake_http_post(url, *, headers, json_body, timeout_seconds):
            return {"status_code": 500, "json": {"id": ""}}

        result = run_phase28_closed_loop_pilot(
            root=tmp_path,
            controlled_live_projection=True,
            env={"DISCORD_BOT_TOKEN": "fake-token-for-test"},
            discord_http_post=fake_http_post,
            target_profile="aicompanyassistant",
            target_channel_id="home:aicompanyassistant:#일일-브리핑",
        )

        assert result.ok is False
        assert result.status == ClosedLoopStatus.FAIL
        assert result.projection_status == "failed"
        assert "live_projection_failed" in result.error

    def test_controlled_live_projection_blocked_makes_top_level_not_ok(
        self,
        tmp_path,
    ):
        calls = []

        def fake_http_post(url, *, headers, json_body, timeout_seconds):
            calls.append(url)
            return {"status_code": 200, "json": {"id": "should-not-post"}}

        result = run_phase28_closed_loop_pilot(
            root=tmp_path,
            controlled_live_projection=True,
            env={"DISCORD_BOT_TOKEN": "fake-token-for-test"},
            discord_http_post=fake_http_post,
            target_profile="aicompanyassistant",
            target_channel_id="wrong-channel",
        )

        assert result.ok is False
        assert result.status == ClosedLoopStatus.FAIL
        assert result.projection_status == "blocked"
        assert "channel_not_allowed" in result.error
        assert calls == []

    def test_live_http_exception_is_sanitized(self, tmp_path):
        def fake_http_post(url, *, headers, json_body, timeout_seconds):
            raise RuntimeError("token=supersecret raw failure")

        result = run_phase28_closed_loop_pilot(
            root=tmp_path,
            controlled_live_projection=True,
            env={"DISCORD_BOT_TOKEN": "fake-token-for-test"},
            discord_http_post=fake_http_post,
            target_profile="aicompanyassistant",
            target_channel_id="home:aicompanyassistant:#일일-브리핑",
        )

        assert result.ok is False
        assert result.projection_status == "failed"
        assert "discord_http_exception" in result.error
        assert "supersecret" not in result.error
        assert "raw failure" not in result.error
