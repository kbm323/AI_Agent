from src.runtime_architecture_v2.command_surface import (
    CommandSurfaceDecision,
    CommandSurfaceMode,
    HermesGatewayCommandSurfacePolicy,
)


def test_phase25_current_policy_prioritizes_hermes_gateway_before_standalone_slash():
    policy = HermesGatewayCommandSurfacePolicy.current_verified()

    assert policy.priority_order == (
        CommandSurfaceMode.HERMES_EXISTING_GATEWAY,
        CommandSurfaceMode.HERMES_SUPPORTED_CUSTOM_SURFACE,
        CommandSurfaceMode.BOT_MENTION_NATURAL_LANGUAGE,
        CommandSurfaceMode.SEPARATE_STANDALONE_SLASH_ADAPTER,
    )
    assert policy.standalone_slash_adapter_enabled is False
    assert policy.interaction_endpoint_enabled is False
    assert policy.permission_mutation_allowed is False
    assert policy.administrator_allowed is False


def test_phase25_allows_hermes_gateway_and_mention_surface_only_when_safe():
    policy = HermesGatewayCommandSurfacePolicy.current_verified()

    gateway_decision = policy.evaluate(
        requested_surface=CommandSurfaceMode.HERMES_EXISTING_GATEWAY,
        require_mention=True,
        thread_require_mention=True,
        free_response_channels=(),
    )
    mention_decision = policy.evaluate(
        requested_surface=CommandSurfaceMode.BOT_MENTION_NATURAL_LANGUAGE,
        require_mention=True,
        thread_require_mention=True,
        free_response_channels=(),
    )

    assert gateway_decision == CommandSurfaceDecision(True, "allowed")
    assert mention_decision == CommandSurfaceDecision(True, "allowed")


def test_phase25_blocks_standalone_slash_until_explicit_adapter_is_enabled():
    policy = HermesGatewayCommandSurfacePolicy.current_verified()

    decision = policy.evaluate(
        requested_surface=CommandSurfaceMode.SEPARATE_STANDALONE_SLASH_ADAPTER,
        require_mention=True,
        thread_require_mention=True,
        free_response_channels=(),
    )

    assert decision == CommandSurfaceDecision(
        False, "standalone_slash_adapter_deferred"
    )


def test_phase25_fails_closed_on_free_response_or_missing_mention_gate():
    policy = HermesGatewayCommandSurfacePolicy.current_verified()

    assert policy.evaluate(
        requested_surface=CommandSurfaceMode.HERMES_EXISTING_GATEWAY,
        require_mention=False,
        thread_require_mention=True,
        free_response_channels=(),
    ) == CommandSurfaceDecision(False, "mention_gate_required")
    assert policy.evaluate(
        requested_surface=CommandSurfaceMode.HERMES_EXISTING_GATEWAY,
        require_mention=True,
        thread_require_mention=False,
        free_response_channels=(),
    ) == CommandSurfaceDecision(False, "mention_gate_required")
    assert policy.evaluate(
        requested_surface=CommandSurfaceMode.HERMES_EXISTING_GATEWAY,
        require_mention=True,
        thread_require_mention=True,
        free_response_channels=("1505600166676271244",),
    ) == CommandSurfaceDecision(False, "free_response_not_allowed")


def test_phase25_fails_closed_when_interaction_endpoint_is_enabled():
    policy = HermesGatewayCommandSurfacePolicy.current_verified()
    endpoint_policy = HermesGatewayCommandSurfacePolicy(
        priority_order=policy.priority_order,
        interaction_endpoint_enabled=True,
    )

    for surface in CommandSurfaceMode:
        assert endpoint_policy.evaluate(
            requested_surface=surface,
            require_mention=True,
            thread_require_mention=True,
            free_response_channels=(),
        ) == CommandSurfaceDecision(False, "interaction_endpoint_not_in_scope")


def test_phase25_fails_closed_on_permission_admin_or_unknown_surface():
    policy = HermesGatewayCommandSurfacePolicy.current_verified()
    permission_policy = HermesGatewayCommandSurfacePolicy(
        priority_order=policy.priority_order,
        permission_mutation_allowed=True,
    )
    admin_policy = HermesGatewayCommandSurfacePolicy(
        priority_order=policy.priority_order,
        administrator_allowed=True,
    )

    assert permission_policy.evaluate(
        requested_surface=CommandSurfaceMode.HERMES_EXISTING_GATEWAY,
        require_mention=True,
        thread_require_mention=True,
        free_response_channels=(),
    ) == CommandSurfaceDecision(False, "permission_mutation_not_allowed")
    assert admin_policy.evaluate(
        requested_surface=CommandSurfaceMode.HERMES_EXISTING_GATEWAY,
        require_mention=True,
        thread_require_mention=True,
        free_response_channels=(),
    ) == CommandSurfaceDecision(False, "administrator_not_allowed")
    assert policy.evaluate(
        requested_surface="custom_slash_command",
        require_mention=True,
        thread_require_mention=True,
        free_response_channels=(),
    ) == CommandSurfaceDecision(False, "surface_not_allowed")


def test_phase25_report_marks_interaction_security_and_slash_registration_deferred():
    report = HermesGatewayCommandSurfacePolicy.current_verified().verification_report()

    assert report["phase"] == "Phase 25"
    assert (
        report["gate_1_discord_interaction_security"]
        == "DEFERRED_NO_LIVE_INTERACTION_ENDPOINT"
    )
    assert (
        report["gate_4_slash_command_registration"]
        == "DEFERRED_STANDALONE_SLASH_NOT_DEFAULT"
    )
    assert report["default_surface"] == "hermes_existing_gateway"
    assert (
        report["standalone_slash_commands"]
        == "out_of_scope_until_explicit_adapter_approval"
    )
    assert report["live_mutation"] == "none"
