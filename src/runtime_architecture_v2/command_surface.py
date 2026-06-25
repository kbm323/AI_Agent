"""Phase 25 Hermes-first Discord command surface verification.

This module is a policy/report layer only. It does not register Discord slash
commands, open an interaction endpoint, mutate bot permissions, or replace the
Hermes Gateway. The intent is to make the current Hermes-first command posture
machine-checkable before later live smoke phases.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, unique


@unique
class CommandSurfaceMode(StrEnum):
    """Ordered command-surface options from the canonical architecture doc."""

    HERMES_EXISTING_GATEWAY = "hermes_existing_gateway"
    HERMES_SUPPORTED_CUSTOM_SURFACE = "hermes_supported_custom_surface"
    BOT_MENTION_NATURAL_LANGUAGE = "bot_mention_natural_language"
    SEPARATE_STANDALONE_SLASH_ADAPTER = "separate_standalone_slash_adapter"


@dataclass(frozen=True)
class CommandSurfaceDecision:
    """Allow/deny result for a requested command surface."""

    allowed: bool
    reason: str


@dataclass(frozen=True)
class HermesGatewayCommandSurfacePolicy:
    """Phase 25 command-surface guardrail.

    The policy preserves the user's Hermes-first decision:
    - use Hermes existing Discord Gateway / command behavior first;
    - keep natural-language bot mention as the default domain command path;
    - keep standalone slash commands deferred unless a separate adapter is
      explicitly approved later;
    - do not expand permissions, enable Administrator, disable mention gates,
      or add free-response channels.
    """

    priority_order: tuple[CommandSurfaceMode, ...]
    standalone_slash_adapter_enabled: bool = False
    interaction_endpoint_enabled: bool = False
    permission_mutation_allowed: bool = False
    administrator_allowed: bool = False

    @classmethod
    def current_verified(cls) -> HermesGatewayCommandSurfacePolicy:
        """Return the current verified Hermes-first command-surface posture."""

        return cls(
            priority_order=(
                CommandSurfaceMode.HERMES_EXISTING_GATEWAY,
                CommandSurfaceMode.HERMES_SUPPORTED_CUSTOM_SURFACE,
                CommandSurfaceMode.BOT_MENTION_NATURAL_LANGUAGE,
                CommandSurfaceMode.SEPARATE_STANDALONE_SLASH_ADAPTER,
            )
        )

    def evaluate(
        self,
        *,
        requested_surface: CommandSurfaceMode | str,
        require_mention: bool,
        thread_require_mention: bool,
        free_response_channels: tuple[str, ...],
    ) -> CommandSurfaceDecision:
        """Fail closed unless the requested surface preserves Hermes-first safety."""

        try:
            surface = CommandSurfaceMode(requested_surface)
        except ValueError:
            return CommandSurfaceDecision(False, "surface_not_allowed")

        if surface not in self.priority_order:
            return CommandSurfaceDecision(False, "surface_not_allowed")
        if self.interaction_endpoint_enabled:
            return CommandSurfaceDecision(False, "interaction_endpoint_not_in_scope")
        if self.permission_mutation_allowed:
            return CommandSurfaceDecision(False, "permission_mutation_not_allowed")
        if self.administrator_allowed:
            return CommandSurfaceDecision(False, "administrator_not_allowed")
        if not require_mention or not thread_require_mention:
            return CommandSurfaceDecision(False, "mention_gate_required")
        if free_response_channels:
            return CommandSurfaceDecision(False, "free_response_not_allowed")
        if (
            surface is CommandSurfaceMode.SEPARATE_STANDALONE_SLASH_ADAPTER
            and not self.standalone_slash_adapter_enabled
        ):
            return CommandSurfaceDecision(False, "standalone_slash_adapter_deferred")
        return CommandSurfaceDecision(True, "allowed")

    def verification_report(self) -> dict[str, str]:
        """Return a stable Phase 25 report shape for docs and audits."""

        default_surface = self.priority_order[0]
        return {
            "phase": "Phase 25",
            "name": "Hermes Gateway Command Surface Verification",
            "default_surface": default_surface.value,
            "gate_1_discord_interaction_security": (
                "DEFERRED_NO_LIVE_INTERACTION_ENDPOINT"
            ),
            "gate_4_slash_command_registration": (
                "DEFERRED_STANDALONE_SLASH_NOT_DEFAULT"
            ),
            "standalone_slash_commands": (
                "out_of_scope_until_explicit_adapter_approval"
            ),
            "mention_gate": "required",
            "thread_mention_gate": "required",
            "free_response_channels": "not_allowed",
            "administrator_permission": "not_allowed",
            "live_mutation": "none",
        }


__all__ = [
    "CommandSurfaceDecision",
    "CommandSurfaceMode",
    "HermesGatewayCommandSurfacePolicy",
]
