"""Handler Routing Module — Sub-AC 1.1.3

Inspects validated interaction payloads (post Ed25519 signature verification),
extracts the interaction type and command name, and routes to the appropriate
handler: PING responder, meeting request parser, or unknown-command responder.

Pipeline position::

    Discord HTTP request (raw bytes + headers)
        │
        ▼
    verify_request_signature()             ← Sub-AC 1.1.2
        │  SignatureResult.valid=True
        ▼
    route_interaction()                    ← Sub-AC 1.1.3 (this module)
        │  RouteResult
        ▼
    ┌──────────┬────────────────┬──────────────────┐
    ▼          ▼                ▼                  ▼
    PING       meeting          status             unknown
    responder  orchestrator     handler            responder
              (Sub-AC 1b-iii)  (future)

Design
------
- **Post-validation layer** — receives payloads whose Ed25519 signature has
  already been verified by ``interaction_signature_validator``.  This module
  assumes cryptographic authenticity and focuses on semantic routing.
- **Pure-in-memory** — no filesystem I/O, no CLI calls, no LLM invocations.
  The module is fully testable with mock interaction payloads and handler
  stubs.
- **Handler registry** — a mutable ``HandlerRegistry`` maps command names
  to callable handlers plus special entries for PING and unknown commands.
  The default registry is created via ``create_default_registry()``.
- **Structured results** — every call to ``route_interaction()`` returns a
  ``RouteResult`` — never raises exceptions for invalid payloads.  Callers
  branch on ``.success``.
- **Follows project patterns** — discriminated result type, StrEnum/IntEnum,
  error-as-value, Protocol for handler callables.

Interaction types recognised
----------------------------
- **PING** (type 1) — Discord endpoint verification.  Routed to the PING
  handler, which returns ``{"type": 1}`` (PONG).
- **APPLICATION_COMMAND** (type 2) — Slash command.  Command name is
  extracted from ``data.name`` and looked up in the registry.  Falls
  back to the unknown handler when no matching command handler is
  registered.
- **MESSAGE_COMPONENT** (type 3) — Button/select menu interactions
  (future support — routed to unknown handler).
- **APPLICATION_COMMAND_AUTOCOMPLETE** (type 4) — Autocomplete requests
  (future support — routed to unknown handler).
- **MODAL_SUBMIT** (type 5) — Modal form submissions
  (future support — routed to unknown handler).

Testability
-----------
Every routing scenario is exercisable with mock interaction payloads and
handler stubs — no network, no signature keys, no Discord application
registration required::

    registry = HandlerRegistry()
    registry.register("meeting", mock_meeting_handler)
    registry.register_ping(mock_ping_handler)
    registry.register_unknown(mock_unknown_handler)

    result = route_interaction(mock_ping_payload, registry=registry)
    assert result.success
    assert result.route.handler_key == "ping"
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import IntEnum, unique
from typing import Any, Callable, Dict, Optional, Protocol


# ═══════════════════════════════════════════════════════════════════════════
# Discord interaction types
# ═══════════════════════════════════════════════════════════════════════════


@unique
class DiscordInteractionType(IntEnum):
    """Discord interaction types as defined in the Discord API.

    https://discord.com/developers/docs/interactions/receiving-and-responding#interaction-object-interaction-type
    """

    PING = 1
    """Gateway URL verification — must respond with type 1 (PONG)."""

    APPLICATION_COMMAND = 2
    """Slash command or context menu interaction."""

    MESSAGE_COMPONENT = 3
    """Button, select menu, or other message component interaction."""

    APPLICATION_COMMAND_AUTOCOMPLETE = 4
    """Autocomplete request for slash command options."""

    MODAL_SUBMIT = 5
    """Modal form submission interaction."""


# Human-readable labels for interaction types (used in error messages/logs).
_INTERACTION_TYPE_LABELS: dict[int, str] = {
    1: "PING",
    2: "APPLICATION_COMMAND",
    3: "MESSAGE_COMPONENT",
    4: "APPLICATION_COMMAND_AUTOCOMPLETE",
    5: "MODAL_SUBMIT",
}


# ═══════════════════════════════════════════════════════════════════════════
# Handler callable protocol
# ═══════════════════════════════════════════════════════════════════════════


class InteractionHandler(Protocol):
    """Protocol for interaction handler callables.

    A handler receives the full interaction payload dict and returns a
    Discord-compatible interaction response dict.  Handlers may be:

    - **Real** — ``orchestrate_slash_command()``, a PING responder, etc.
    - **Stub** — for testing, a simple predicate callable that records
      invocations and returns a canned response.
    """

    def __call__(self, payload: dict[str, Any]) -> dict[str, Any]: ...


# ═══════════════════════════════════════════════════════════════════════════
# Handler route — the routing decision
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class HandlerRoute:
    """The result of routing an interaction to a specific handler.

    Produced by ``route_interaction()`` on success.  The Coordinator
    inspects ``handler_key`` and ``handler`` to invoke the correct
    downstream pipeline.

    Attributes:
        interaction_type: The Discord interaction type (PING=1,
            APPLICATION_COMMAND=2, etc.).
        command_name: The slash command name extracted from
            ``data.name`` (empty for PING and non-command types).
        handler_key: A stable string key identifying the resolved
            handler — one of ``"ping"``, ``"meeting"``, ``"unknown"``,
            or a custom key registered by the caller.
        handler: The callable handler function.
        raw_payload: The original interaction payload dict (for
            audit/debugging).
    """

    interaction_type: DiscordInteractionType
    """The Discord interaction type."""

    command_name: str = ""
    """The slash command name (empty for non-command types)."""

    handler_key: str = ""
    """Stable key identifying the resolved handler."""

    handler: InteractionHandler | None = None
    """The resolved handler callable (None when using default registry)."""

    raw_payload: dict[str, Any] = field(default_factory=dict)
    """The original interaction payload dict."""


# ═══════════════════════════════════════════════════════════════════════════
# Route result — success or structured error
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class RouteResult:
    """Result of ``route_interaction()``.

    Mirrors the ``InteractionParseResult`` / ``OrchestratorResult``
    pattern — every routing decision, whether successful or not, returns
    a structured, inspectable result.

    When ``success`` is True:
      - ``route`` carries the resolved ``HandlerRoute``.

    When ``success`` is False:
      - ``error`` holds a human-readable description.
      - ``error_code`` holds a machine-readable error code.

    Attributes:
        success: True when routing was successful.
        route: The resolved ``HandlerRoute`` (success only).
        error: Human-readable rejection reason (failure only).
        error_code: Machine-readable error code (failure only).
    """

    success: bool
    """True when the interaction was successfully routed."""

    route: HandlerRoute | None = None
    """The resolved route (success only)."""

    error: str = ""
    """Human-readable rejection reason (failure only)."""

    error_code: str = ""
    """Machine-readable error code (failure only)."""


# ═══════════════════════════════════════════════════════════════════════════
# Handler registry — mutable command → handler map
# ═══════════════════════════════════════════════════════════════════════════


class HandlerRegistry:
    """Mutable registry mapping command names to handler functions.

    The registry is the **single source of truth** for which handler
    responds to which command.  It supports:

    - **Command handlers** — mapped by command name (e.g. ``"meeting"``).
    - **PING handler** — invoked for PING (type 1) interactions.
    - **Unknown-command handler** — invoked when no command handler
      matches the name, or for unsupported interaction types.

    All handlers are ``InteractionHandler`` callables.  The registry is
    intentionally mutable so that tests can register mock/stub handlers
    and production code can wire up real pipeline functions.

    Usage::

        registry = HandlerRegistry()
        registry.register("meeting", real_meeting_orchestrator)
        registry.register_ping(ping_responder)
        registry.register_unknown(unknown_command_responder)

        result = route_interaction(payload, registry=registry)
    """

    def __init__(self) -> None:
        self._command_handlers: dict[str, InteractionHandler] = {}
        self._ping_handler: InteractionHandler | None = None
        self._unknown_handler: InteractionHandler | None = None

    # ── Registration ──────────────────────────────────────────────────

    def register(self, command_name: str, handler: InteractionHandler) -> None:
        """Register a handler for a specific slash command name.

        Args:
            command_name: The slash command name (e.g. ``"meeting"``,
                ``"status"``).  Case-sensitive — must match exactly.
            handler: The callable that processes this command's
                interactions.

        Raises:
            TypeError: If ``command_name`` is not a non-empty string.
        """
        if not isinstance(command_name, str) or not command_name.strip():
            raise TypeError("command_name must be a non-empty string")
        self._command_handlers[command_name] = handler

    def register_ping(self, handler: InteractionHandler) -> None:
        """Register the handler for PING (type 1) interactions.

        When set, every PING interaction is routed to this handler.
        When not set (None), the router returns a built-in PONG
        response automatically.

        Args:
            handler: The callable that processes PING interactions.
        """
        self._ping_handler = handler

    def register_unknown(self, handler: InteractionHandler) -> None:
        """Register the fallback handler for unrecognised commands.

        When a command name has no registered handler, or the
        interaction type is not APPLICATION_COMMAND (and not PING),
        this handler is invoked.

        Args:
            handler: The callable that processes unknown/unrecognised
                commands.
        """
        self._unknown_handler = handler

    # ── Lookup ────────────────────────────────────────────────────────

    def get(self, command_name: str) -> InteractionHandler | None:
        """Look up the handler for *command_name*.

        Args:
            command_name: The slash command name.

        Returns:
            The registered handler, or ``None`` if no handler is
            registered for this command name.
        """
        return self._command_handlers.get(command_name)

    def get_ping(self) -> InteractionHandler | None:
        """Return the registered PING handler, or ``None``."""
        return self._ping_handler

    def get_unknown(self) -> InteractionHandler | None:
        """Return the registered unknown-command handler, or ``None``."""
        return self._unknown_handler

    # ── Introspection ─────────────────────────────────────────────────

    @property
    def registered_commands(self) -> frozenset[str]:
        """Return the set of registered command names (immutable view)."""
        return frozenset(self._command_handlers.keys())

    @property
    def has_ping_handler(self) -> bool:
        """True when a custom PING handler is registered."""
        return self._ping_handler is not None

    @property
    def has_unknown_handler(self) -> bool:
        """True when an unknown-command handler is registered."""
        return self._unknown_handler is not None

    def __len__(self) -> int:
        """Return the number of registered command handlers."""
        return len(self._command_handlers)

    def __contains__(self, command_name: str) -> bool:
        """True when *command_name* has a registered handler."""
        return command_name in self._command_handlers


# ═══════════════════════════════════════════════════════════════════════════
# Default handlers
# ═══════════════════════════════════════════════════════════════════════════


def _default_ping_handler(payload: dict[str, Any]) -> dict[str, Any]:
    """Built-in PING responder — returns Discord PONG response.

    Per Discord's documentation, a PING interaction must be acknowledged
    with ``{"type": 1}`` (PONG).
    """
    return {"type": 1}


def _default_unknown_handler(payload: dict[str, Any]) -> dict[str, Any]:
    """Built-in unknown-command responder.

    Returns a user-facing error message indicating the command is not
    recognised.  Uses Discord interaction response type 4
    (CHANNEL_MESSAGE_WITH_SOURCE) so the user sees the error inline.
    """
    command_name = ""
    data = payload.get("data")
    if isinstance(data, dict):
        command_name = data.get("name", "")

    content = (
        f"❌ **Unknown command** `/{(command_name or 'unknown')}`\n"
        "This command is not recognised by the AI_Agent meeting system.\n"
        "Try `/meeting` to start a new meeting."
    )

    return {
        "type": 4,  # CHANNEL_MESSAGE_WITH_SOURCE
        "data": {"content": content},
    }


# ═══════════════════════════════════════════════════════════════════════════
# Default handler registry factory
# ═══════════════════════════════════════════════════════════════════════════


def create_default_registry() -> HandlerRegistry:
    """Create a ``HandlerRegistry`` populated with built-in handlers.

    The default registry includes:

    - **PING handler** — the built-in ``_default_ping_handler`` that
      returns ``{"type": 1}`` (PONG).
    - **Unknown-command handler** — the built-in
      ``_default_unknown_handler`` that returns a user-facing error
      message.

    Callers should register application-specific command handlers
    (e.g. ``"meeting"``) after creating the default registry::

        registry = create_default_registry()
        registry.register("meeting", my_meeting_orchestrator)
        registry.register("status", my_status_handler)

    Returns:
        A new ``HandlerRegistry`` with PING and unknown-command
        handlers pre-registered.
    """
    registry = HandlerRegistry()
    registry.register_ping(_default_ping_handler)
    registry.register_unknown(_default_unknown_handler)
    return registry


# ═══════════════════════════════════════════════════════════════════════════
# Public API — route_interaction()
# ═══════════════════════════════════════════════════════════════════════════


def route_interaction(
    payload: dict[str, Any] | str | bytes,
    *,
    registry: HandlerRegistry | None = None,
) -> RouteResult:
    """Route a validated interaction payload to the appropriate handler.

    This is the **single entry point** for Sub-AC 1.1.3.  It receives a
    validated interaction payload (post Ed25519 signature verification),
    extracts the interaction type and command name, and returns a
    ``RouteResult`` with the resolved ``HandlerRoute``.

    **Routing logic**:

    1. **Normalise** the payload to a ``dict`` (accepts ``bytes`` and
       ``str`` JSON).
    2. **Validate** the structural envelope (``type`` field must be
       present and be a recognised ``DiscordInteractionType``).
    3. **Route** by interaction type:

       - ``PING`` (1) → PING handler (custom or built-in PONG).
       - ``APPLICATION_COMMAND`` (2) → lookup command name in registry.
         If found, route to the registered handler; otherwise route to
         the unknown-command handler.
       - ``MESSAGE_COMPONENT`` (3), ``AUTOCOMPLETE`` (4),
         ``MODAL_SUBMIT`` (5) → route to unknown-command handler
         (future support).

    4. **Fallback** — when no registry is provided, all routes resolve
       to ``handler_key="unknown"`` with no handler callable.  Callers
       should provide a registry for production use.

    Args:
        payload: Validated Discord interaction payload.  May be:
            - A ``dict`` (already parsed JSON).
            - A ``str`` (JSON string).
            - ``bytes`` (UTF-8 encoded JSON).
        registry: Optional ``HandlerRegistry`` for command→handler
            mapping.  When ``None``, routes are resolved but no
            handler callable is attached.

    Returns:
        ``RouteResult`` — inspect ``.success`` to branch; access
        ``.route`` when True, ``.error`` / ``.error_code`` when False.

    Examples:
        PING interaction routed to built-in PONG handler::

            >>> payload = {"type": 1, "id": "abc", "token": "tok"}
            >>> reg = create_default_registry()
            >>> result = route_interaction(payload, registry=reg)
            >>> result.success
            True
            >>> result.route.handler_key
            'ping'
            >>> result.route.handler(payload)
            {'type': 1}

        Meeting slash command routed to custom handler::

            >>> reg = create_default_registry()
            >>> def mock_meeting(p): return {"type": 4, "data": {"content": "ok"}}
            >>> reg.register("meeting", mock_meeting)
            >>> payload = {"type": 2, "id": "abc", "token": "tok",
            ...            "data": {"name": "meeting", "type": 1}}
            >>> result = route_interaction(payload, registry=reg)
            >>> result.success
            True
            >>> result.route.handler_key
            'meeting'

        Unknown command routed to unknown-command handler::

            >>> reg = create_default_registry()
            >>> payload = {"type": 2, "id": "abc", "token": "tok",
            ...            "data": {"name": "play_music", "type": 1}}
            >>> result = route_interaction(payload, registry=reg)
            >>> result.success
            True
            >>> result.route.handler_key
            'unknown'

        Invalid payload — missing type::

            >>> result = route_interaction({"id": "abc", "token": "tok"})
            >>> result.success
            False
            >>> result.error_code
            'MISSING_TYPE'
    """
    # ── Step 1: Normalise payload to dict ─────────────────────────────
    payload_dict = _normalise_payload(payload)
    if isinstance(payload_dict, RouteResult):
        return payload_dict

    # ── Step 2: Validate structural envelope ──────────────────────────
    envelope_error = _validate_routing_envelope(payload_dict)
    if envelope_error is not None:
        return envelope_error

    # ── Step 3: Resolve interaction type ──────────────────────────────
    raw_type = payload_dict["type"]
    try:
        interaction_type = DiscordInteractionType(raw_type)
    except ValueError:
        return RouteResult(
            success=False,
            error=f"Unknown interaction type: {raw_type}",
            error_code="UNKNOWN_INTERACTION_TYPE",
        )

    # ── Step 4: Route by interaction type ─────────────────────────────
    return _route_by_type(payload_dict, interaction_type, registry)


# ═══════════════════════════════════════════════════════════════════════════
# Internal: payload normalisation
# ═══════════════════════════════════════════════════════════════════════════


def _normalise_payload(
    payload: dict[str, Any] | str | bytes,
) -> dict[str, Any] | RouteResult:
    """Normalise the payload to a dict, returning an error RouteResult on failure."""
    if isinstance(payload, bytes):
        try:
            payload_str = payload.decode("utf-8")
        except UnicodeDecodeError:
            return RouteResult(
                success=False,
                error="Raw payload is not valid UTF-8",
                error_code="INVALID_UTF8",
            )
        return _parse_json_string(payload_str)
    elif isinstance(payload, str):
        return _parse_json_string(payload)
    else:
        if not isinstance(payload, dict):
            return RouteResult(
                success=False,
                error="Interaction payload is not a JSON object",
                error_code="NOT_A_DICT",
            )
        return payload


def _parse_json_string(raw: str) -> dict[str, Any] | RouteResult:
    """Parse a JSON string into a dict, returning an error RouteResult on failure."""
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        return RouteResult(
            success=False,
            error=f"Failed to parse interaction JSON body: {exc}",
            error_code="INVALID_JSON",
        )
    if not isinstance(result, dict):
        return RouteResult(
            success=False,
            error="Interaction payload is not a JSON object",
            error_code="NOT_A_DICT",
        )
    return result


# ═══════════════════════════════════════════════════════════════════════════
# Internal: envelope validation
# ═══════════════════════════════════════════════════════════════════════════


def _validate_routing_envelope(payload: dict[str, Any]) -> RouteResult | None:
    """Validate the required envelope fields for routing.

    Returns None when all checks pass, otherwise a RouteResult error.
    """
    # id field
    payload_id = payload.get("id")
    if not isinstance(payload_id, str) or not payload_id:
        return RouteResult(
            success=False,
            error="Interaction payload missing valid 'id'",
            error_code="MISSING_ID",
        )

    # token field
    payload_token = payload.get("token")
    if not isinstance(payload_token, str) or not payload_token:
        return RouteResult(
            success=False,
            error="Interaction payload missing valid 'token'",
            error_code="MISSING_TOKEN",
        )

    # type field
    raw_type = payload.get("type")
    if raw_type is None:
        return RouteResult(
            success=False,
            error="Interaction payload missing 'type' field",
            error_code="MISSING_TYPE",
        )
    if not isinstance(raw_type, int):
        return RouteResult(
            success=False,
            error=(
                f"Interaction type must be an integer, "
                f"got {type(raw_type).__name__}"
            ),
            error_code="INVALID_TYPE",
        )

    return None


# ═══════════════════════════════════════════════════════════════════════════
# Internal: route by interaction type
# ═══════════════════════════════════════════════════════════════════════════


def _route_by_type(
    payload: dict[str, Any],
    interaction_type: DiscordInteractionType,
    registry: HandlerRegistry | None,
) -> RouteResult:
    """Route the interaction based on its type.

    Returns a RouteResult with the resolved HandlerRoute.
    """
    # ── PING ──────────────────────────────────────────────────────────
    if interaction_type == DiscordInteractionType.PING:
        handler = registry.get_ping() if registry is not None else None
        route = HandlerRoute(
            interaction_type=interaction_type,
            command_name="",
            handler_key="ping",
            handler=handler,
            raw_payload=payload,
        )
        return RouteResult(success=True, route=route)

    # ── APPLICATION_COMMAND ───────────────────────────────────────────
    if interaction_type == DiscordInteractionType.APPLICATION_COMMAND:
        data = payload.get("data")
        if not isinstance(data, dict):
            return RouteResult(
                success=False,
                error="APPLICATION_COMMAND interaction missing valid 'data' object",
                error_code="MISSING_DATA",
            )

        command_name = data.get("name", "")
        if not isinstance(command_name, str) or not command_name:
            return RouteResult(
                success=False,
                error="APPLICATION_COMMAND data missing valid 'name'",
                error_code="MISSING_COMMAND_NAME",
            )

        # Look up the command handler in the registry
        if registry is not None and command_name in registry:
            handler = registry.get(command_name)
            route = HandlerRoute(
                interaction_type=interaction_type,
                command_name=command_name,
                handler_key=command_name,
                handler=handler,
                raw_payload=payload,
            )
            return RouteResult(success=True, route=route)

        # No handler registered — fall back to unknown handler
        handler = registry.get_unknown() if registry is not None else None
        route = HandlerRoute(
            interaction_type=interaction_type,
            command_name=command_name,
            handler_key="unknown",
            handler=handler,
            raw_payload=payload,
        )
        return RouteResult(success=True, route=route)

    # ── MESSAGE_COMPONENT, AUTOCOMPLETE, MODAL_SUBMIT ─────────────────
    # All other interaction types are routed to the unknown handler.
    # Future support can add dedicated handlers for these types.
    handler = registry.get_unknown() if registry is not None else None
    type_label = _INTERACTION_TYPE_LABELS.get(interaction_type.value, str(interaction_type.value))
    route = HandlerRoute(
        interaction_type=interaction_type,
        command_name="",
        handler_key="unknown",
        handler=handler,
        raw_payload=payload,
    )
    return RouteResult(success=True, route=route)


# ═══════════════════════════════════════════════════════════════════════════
# Exports
# ═══════════════════════════════════════════════════════════════════════════

__all__ = [
    # Enums
    "DiscordInteractionType",
    # Data types
    "HandlerRoute",
    "RouteResult",
    "HandlerRegistry",
    # Protocol
    "InteractionHandler",
    # Public API
    "route_interaction",
    "create_default_registry",
    # Default handlers (exposed for testing)
    "_default_ping_handler",
    "_default_unknown_handler",
]
