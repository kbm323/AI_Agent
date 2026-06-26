"""Discord Interaction Webhook Handler — Sub-AC 1.1b

Receives Discord INTERACTION_CREATE events via HTTP webhook, verifies
Ed25519 signatures, handles PING acknowledgment (interaction type 1),
and routes APPLICATION_COMMAND (type 2) to registered command handlers.

Security model:
    https://discord.com/developers/docs/interactions/receiving-and-responding#security-and-authorization

This is the Python-side bridge between Discord's outgoing webhooks and
the AI_Agent meeting orchestration system.  Raw HTTP request data
(body + headers) arrives, signature verification is performed using
PyNaCl, and a structured ``WebhookResult`` is returned with either a
Discord interaction response (PONG or message) or a parsed interaction
ready for the Coordinator pipeline.

Design decisions
----------------
- Signature verification happens *before* any body parsing — we never
  touch untrusted input until the Ed25519 check passes.
- All error paths return structured ``WebhookResult`` objects — no
  silent exceptions escape the handler.
- Command routing uses a registry pattern so handlers can be injected
  without the webhook module depending on the Coordinator directly.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

import nacl.encoding
import nacl.exceptions
import nacl.signing

# ── Discord Interaction Types ─────────────────────────────────────────────


class DiscordInteractionType(IntEnum):
    """Discord interaction types we process.

    Values match the Discord API:
    https://discord.com/developers/docs/interactions/receiving-and-responding#interaction-object-interaction-type
    """

    PING = 1
    APPLICATION_COMMAND = 2


# ── Discord Interaction Response Types ────────────────────────────────────


class DiscordResponseType(IntEnum):
    """Discord interaction callback response types.

    Used in the JSON body returned to Discord's interaction callback URL.
    https://discord.com/developers/docs/interactions/receiving-and-responding#interaction-response-object-interaction-callback-type
    """

    PONG = 1  # ACK a Ping
    CHANNEL_MESSAGE_WITH_SOURCE = 4  # Respond with a message
    DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE = 5  # ACK and respond later
    DEFERRED_UPDATE_MESSAGE = 6  # ACK for component, edit later
    UPDATE_MESSAGE = 7  # Edit a previous component response


# ── Discord Interaction Callback Flags ────────────────────────────────────


class DiscordMessageFlags(IntEnum):
    """Flags for interaction callback data.

    https://discord.com/developers/docs/resources/channel#message-object-message-flags
    """

    EPHEMERAL = 64  # Only the invoking user can see this message
    SUPPRESS_EMBEDS = 4  # Suppress link embeds
    SUPPRESS_NOTIFICATIONS = 4096  # Suppress push notifications


# ── Recognised interaction types (for fast validation) ────────────────────

_VALID_INTERACTION_TYPES: frozenset[int] = frozenset(
    {int(DiscordInteractionType.PING), int(DiscordInteractionType.APPLICATION_COMMAND)}
)


# ═══════════════════════════════════════════════════════════════════════════
# Data types
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class WebhookRequest:
    """Raw webhook request received from Discord's outgoing webhook.

    Captures the three pieces needed for Ed25519 verification:
    the raw body bytes (MUST NOT be decoded before verification),
    the ``X-Signature-Ed25519`` header, and the
    ``X-Signature-Timestamp`` header.
    """

    raw_body: bytes
    """Raw request body — must be the exact bytes Discord sent."""

    signature: str
    """Value of the ``X-Signature-Ed25519`` header (hex-encoded)."""

    timestamp: str
    """Value of the ``X-Signature-Timestamp`` header (ASCII integer)."""


@dataclass(frozen=True)
class InteractionResponse:
    """A Discord interaction callback response.

    This is the JSON body that gets sent back to Discord in response
    to the initial POST.  Discord expects ``{"type": N, "data": {...}}``.
    """

    type: int
    """One of the :class:`DiscordResponseType` values."""

    data: dict[str, Any] | None = None
    """Optional data payload (content, embeds, flags, etc.)."""

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dictionary for Discord."""
        result: dict[str, Any] = {"type": self.type}
        if self.data is not None:
            result["data"] = self.data
        return result

    def to_json(self) -> str:
        """Serialize to a JSON string for the HTTP response body."""
        return json.dumps(self.to_dict(), ensure_ascii=False)


@dataclass(frozen=True)
class ParsedInteraction:
    """A successfully parsed and signature-verified Discord interaction.

    Holds all extracted fields from the interaction payload, ready
    for consumption by the command routing layer and the Coordinator.
    """

    interaction_id: str
    """Unique Discord interaction snowflake ID."""

    interaction_token: str
    """Discord interaction token (valid for 15 minutes)."""

    type: DiscordInteractionType
    """Interaction type (PING or APPLICATION_COMMAND)."""

    user_id: str
    """Discord user ID who triggered the interaction."""

    channel_id: str
    """Discord channel ID where the interaction occurred."""

    guild_id: str = ""
    """Discord guild (server) ID, empty for DMs."""

    command_name: str = ""
    """Command name extracted from the data object (APPLICATION_COMMAND only)."""

    command_options: dict[str, Any] = field(default_factory=dict)
    """Flattened command options keyed by name (APPLICATION_COMMAND only)."""

    raw_payload: dict[str, Any] = field(default_factory=dict)
    """The full, unmodified interaction JSON payload (for debugging)."""


@dataclass(frozen=True)
class WebhookResult:
    """The result of processing a Discord webhook request.

    Pattern: every call to :func:`handle_webhook` returns exactly one
    ``WebhookResult`` — never ``None``, never an exception.  Callers
    inspect ``success`` and branch accordingly.

    When ``success`` is True:
      - ``response`` carries the Discord callback payload (if any).
      - ``parsed_interaction`` carries the extracted fields.

    When ``success`` is False:
      - ``error`` explains why processing failed.
      - ``response`` and ``parsed_interaction`` are ``None``.
    """

    success: bool
    """True when the webhook was successfully processed."""

    response: InteractionResponse | None = None
    """The Discord callback response to send back (PONG / message / deferred)."""

    parsed_interaction: ParsedInteraction | None = None
    """The parsed interaction fields (caller routes to Coordinator)."""

    error: str | None = None
    """Human-readable error description (only populated when success=False)."""


# ═══════════════════════════════════════════════════════════════════════════
# Error types
# ═══════════════════════════════════════════════════════════════════════════


class WebhookHandlerError(Exception):
    """Base exception for all webhook-handler failures.

    Every error carries a machine-readable ``code`` for programmatic
    handling and a human-readable message for logging.
    """

    def __init__(self, message: str, *, code: str = "WEBHOOK_ERROR") -> None:
        super().__init__(message)
        self.code: str = code


class SignatureVerificationError(WebhookHandlerError):
    """Ed25519 signature verification failed.

    May indicate an incorrect public key, a replay attack (stale
    timestamp), or a man-in-the-middle tampering with the request
    body.
    """

    def __init__(
        self, message: str = "Discord interaction signature verification failed"
    ) -> None:
        super().__init__(message, code="SIGNATURE_VERIFICATION_FAILED")


class InvalidPayloadError(WebhookHandlerError):
    """The interaction payload is malformed or missing required fields.

    Discord's contract requires ``id``, ``token``, and ``type`` in
    every interaction object.  If any are missing or the wrong type,
    this error is raised.
    """

    def __init__(self, message: str = "Invalid interaction payload") -> None:
        super().__init__(message, code="INVALID_PAYLOAD")


# ═══════════════════════════════════════════════════════════════════════════
# Ed25519 signature verification
# ═══════════════════════════════════════════════════════════════════════════


def verify_discord_signature(
    raw_body: bytes,
    signature_hex: str,
    timestamp: str,
    public_key_hex: str,
) -> bool:
    """Verify an Ed25519 Discord interaction signature.

    Implements the algorithm documented at:
    https://discord.com/developers/docs/interactions/receiving-and-responding#security-and-authorization

    1. Decode the hex-encoded public key (must be 32 bytes).
    2. Decode the hex-encoded signature (must be 64 bytes).
    3. Construct the signed message: ``timestamp`` as UTF-8 bytes
       concatenated with ``raw_body``.
    4. Verify using the Ed25519 verify key.

    Args:
        raw_body: The raw HTTP request body bytes — must be the exact
                  bytes Discord sent.  Do *not* decode to string first.
        signature_hex: Value of the ``X-Signature-Ed25519`` header
                       (hex-encoded 64-byte Ed25519 signature).
        timestamp: Value of the ``X-Signature-Timestamp`` header
                   (ASCII-encoded decimal string).
        public_key_hex: Your Discord application's public key from
                        the Developer Portal (hex-encoded 32-byte key).

    Returns:
        ``True`` when the signature is valid.

    Raises:
        SignatureVerificationError: If any parameter is missing,
            the keys/signature are not valid hex, the key/signature
            lengths are wrong, or the Ed25519 verification fails.
    """
    # ── Guard: required parameters ──────────────────────────────
    if not signature_hex or not timestamp or not public_key_hex:
        raise SignatureVerificationError(
            "Missing one or more signature verification parameters"
        )

    # ── Decode public key (32 bytes hex) ────────────────────────
    try:
        public_key_bytes = bytes.fromhex(public_key_hex)
    except ValueError:
        raise SignatureVerificationError(
            "Discord public key is not valid hex"
        ) from None

    if len(public_key_bytes) != 32:
        raise SignatureVerificationError(
            f"Discord public key must be 32 bytes (got {len(public_key_bytes)})"
        )

    # ── Decode signature (64 bytes hex) ─────────────────────────
    try:
        signature_bytes = bytes.fromhex(signature_hex)
    except ValueError:
        raise SignatureVerificationError("Discord signature is not valid hex") from None

    if len(signature_bytes) != 64:
        raise SignatureVerificationError(
            f"Invalid Ed25519 signature length: {len(signature_bytes)} (expected 64)"
        )

    # ── Verify ──────────────────────────────────────────────────
    verify_key = nacl.signing.VerifyKey(public_key_bytes)
    message = timestamp.encode("utf-8") + raw_body

    try:
        verify_key.verify(message, signature_bytes)
    except nacl.exceptions.BadSignatureError:
        raise SignatureVerificationError(
            "Discord interaction signature verification failed"
        ) from None

    return True


# ═══════════════════════════════════════════════════════════════════════════
# Payload parsing
# ═══════════════════════════════════════════════════════════════════════════


def parse_interaction_payload(raw_body: bytes) -> dict[str, Any]:
    """Parse the raw HTTP body as a Discord interaction JSON payload.

    Performs structural validation on the required envelope fields
    (``id``, ``token``, ``type``) before returning the dict.

    Args:
        raw_body: The raw HTTP request body bytes.

    Returns:
        The parsed interaction payload as a dictionary.

    Raises:
        InvalidPayloadError: If the body is not valid UTF-8, not valid
            JSON, not a JSON object, or missing required fields.
    """
    # ── Decode UTF-8 ────────────────────────────────────────────
    try:
        body_str = raw_body.decode("utf-8")
    except UnicodeDecodeError:
        raise InvalidPayloadError("Raw body is not valid UTF-8") from None

    # ── Parse JSON ──────────────────────────────────────────────
    try:
        payload = json.loads(body_str)
    except json.JSONDecodeError as exc:
        raise InvalidPayloadError(
            f"Failed to parse interaction JSON body: {exc}"
        ) from None

    if not isinstance(payload, dict):
        raise InvalidPayloadError("Interaction payload is not a JSON object")

    # ── Validate required envelope fields ───────────────────────
    if not isinstance(payload.get("id"), str) or not payload["id"]:
        raise InvalidPayloadError("Interaction payload missing valid 'id'")

    if not isinstance(payload.get("token"), str) or not payload["token"]:
        raise InvalidPayloadError("Interaction payload missing valid 'token'")

    raw_type = payload.get("type")
    if not isinstance(raw_type, int):
        raise InvalidPayloadError("Interaction payload missing or invalid 'type'")

    if raw_type not in _VALID_INTERACTION_TYPES:
        raise InvalidPayloadError(f"Unknown interaction type: {raw_type}")

    return payload


# ═══════════════════════════════════════════════════════════════════════════
# Main webhook handler
# ═══════════════════════════════════════════════════════════════════════════


def handle_webhook(
    request: WebhookRequest,
    public_key: str,
) -> WebhookResult:
    """Process a Discord interaction webhook request end-to-end.

    Pipeline
    --------
    1. **Verify Ed25519 signature** — reject tampered or replayed
       requests before touching the body.
    2. **Parse the JSON payload** — validate structural requirements.
    3. **Route by interaction type** — PING gets a PONG response;
       APPLICATION_COMMAND gets parsed and returned for the caller
       to route to the Coordinator pipeline.

    Args:
        request: The raw webhook request (body bytes + two headers).
        public_key: Your Discord application's public key from the
                    Developer Portal (hex-encoded).

    Returns:
        A ``WebhookResult`` — inspect ``.success`` to determine the
        outcome, ``.response`` for what to send back to Discord,
        and ``.parsed_interaction`` for the Coordinator pipeline.
    """
    # ── Step 1: Verify Ed25519 signature ────────────────────────
    try:
        verify_discord_signature(
            raw_body=request.raw_body,
            signature_hex=request.signature,
            timestamp=request.timestamp,
            public_key_hex=public_key,
        )
    except WebhookHandlerError as exc:
        return WebhookResult(
            success=False,
            error=str(exc),
        )

    # ── Step 2: Parse JSON payload ──────────────────────────────
    try:
        payload = parse_interaction_payload(request.raw_body)
    except WebhookHandlerError as exc:
        return WebhookResult(
            success=False,
            error=str(exc),
        )

    # ── Step 3: Route by interaction type ───────────────────────
    interaction_type = DiscordInteractionType(payload["type"])

    if interaction_type == DiscordInteractionType.PING:
        return _handle_ping(payload)

    if interaction_type == DiscordInteractionType.APPLICATION_COMMAND:
        return _handle_application_command(payload)

    # Should never reach here — parse_interaction_payload rejects
    # unknown types before we get here.
    return WebhookResult(
        success=False,
        error=f"Unhandled interaction type: {payload['type']}",
    )


# ═══════════════════════════════════════════════════════════════════════════
# Interaction type handlers
# ═══════════════════════════════════════════════════════════════════════════


def _handle_ping(payload: dict[str, Any]) -> WebhookResult:
    """Handle Discord PING interaction — endpoint verification.

    Discord sends a type=1 interaction when you save the Interactions
    Endpoint URL in the Developer Portal.  The correct response is
    ``{"type": 1}`` (PONG).
    """
    response = InteractionResponse(type=int(DiscordResponseType.PONG))

    parsed = ParsedInteraction(
        interaction_id=payload["id"],
        interaction_token=payload["token"],
        type=DiscordInteractionType.PING,
        user_id=_extract_user_id(payload),
        channel_id=_extract_channel_id(payload),
        guild_id=payload.get("guild_id", ""),
        raw_payload=payload,
    )

    return WebhookResult(
        success=True,
        response=response,
        parsed_interaction=parsed,
    )


def _handle_application_command(
    payload: dict[str, Any],
) -> WebhookResult:
    """Handle Discord APPLICATION_COMMAND interaction.

    Extracts the command name and options from the payload's ``data``
    object.  Does NOT send an immediate response — the caller is
    expected to use the returned ``ParsedInteraction`` to route the
    command and decide on the appropriate response strategy (immediate,
    deferred, or error).

    If the ``data`` field is missing or not a dict, returns a failure
    result — a well-formed APPLICATION_COMMAND always carries a
    ``data`` object per the Discord API spec.
    """
    data = payload.get("data")

    if not isinstance(data, dict):
        return WebhookResult(
            success=False,
            error="APPLICATION_COMMAND interaction missing valid 'data' object",
        )

    command_name = data.get("name", "")
    options = _flatten_command_options(data.get("options", []))

    parsed = ParsedInteraction(
        interaction_id=payload["id"],
        interaction_token=payload["token"],
        type=DiscordInteractionType.APPLICATION_COMMAND,
        user_id=_extract_user_id(payload),
        channel_id=_extract_channel_id(payload),
        guild_id=payload.get("guild_id", ""),
        command_name=command_name,
        command_options=options,
        raw_payload=payload,
    )

    return WebhookResult(
        success=True,
        parsed_interaction=parsed,
    )


# ═══════════════════════════════════════════════════════════════════════════
# Command routing
# ═══════════════════════════════════════════════════════════════════════════

#: Registry mapping command names to handler callables.
#: Populated via :func:`register_command_handler`.
_command_handlers: dict[str, Callable[[ParsedInteraction], InteractionResponse]] = {}


def register_command_handler(
    command_name: str,
    handler: Callable[[ParsedInteraction], InteractionResponse],
) -> None:
    """Register a handler for a specific Discord slash command.

    The handler receives a fully-parsed ``ParsedInteraction`` and must
    return an ``InteractionResponse`` suitable for sending back to
    Discord.  This indirection keeps the webhook module decoupled from
    the Coordinator — the application bootstrap wires them together.

    Args:
        command_name: Discord command name (e.g. ``"meeting"``, ``"cancel"``).
        handler: Callable that accepts a ``ParsedInteraction`` and
                 returns an ``InteractionResponse``.

    Raises:
        ValueError: If ``command_name`` is empty.
    """
    if not command_name or not command_name.strip():
        raise ValueError("command_name must not be empty")
    _command_handlers[command_name] = handler


def unregister_command_handler(command_name: str) -> bool:
    """Remove a command handler from the registry.

    Returns ``True`` if the command was registered and removed,
    ``False`` if it was not found.
    """
    if command_name in _command_handlers:
        del _command_handlers[command_name]
        return True
    return False


def clear_command_handlers() -> None:
    """Remove all registered command handlers (useful for testing)."""
    _command_handlers.clear()


def route_command(parsed: ParsedInteraction) -> InteractionResponse:
    """Route a parsed APPLICATION_COMMAND to its registered handler.

    Looks up ``parsed.command_name`` in the handler registry and
    invokes the matching callable.  If no handler is registered or
    the handler raises an exception, an ephemeral error message is
    returned instead.

    Args:
        parsed: A parsed APPLICATION_COMMAND interaction.

    Returns:
        An ``InteractionResponse`` suitable for the Discord callback.
        Errors produce an ephemeral message (visible only to the
        invoking user).
    """
    handler = _command_handlers.get(parsed.command_name)
    if handler is None:
        return build_error_response(
            f"Unknown command: /{parsed.command_name}",
            ephemeral=True,
        )

    try:
        return handler(parsed)
    except Exception:
        return build_error_response(
            "Command handler error: command_handler_exception",
            ephemeral=True,
        )


# ═══════════════════════════════════════════════════════════════════════════
# Response builders
# ═══════════════════════════════════════════════════════════════════════════


def build_deferred_response() -> InteractionResponse:
    """Build a *deferred* response (type 5).

    Use this when the command will take longer than 3 seconds to
    process.  It ACKs the interaction immediately and tells Discord
    to show a "Bot is thinking..." state.  The actual response is
    sent later via the interaction webhook URL.

    Returns:
        ``InteractionResponse`` with type 5 and no data.
    """
    return InteractionResponse(
        type=int(DiscordResponseType.DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE)
    )


def build_error_response(
    message: str,
    *,
    ephemeral: bool = True,
) -> InteractionResponse:
    """Build an error message response (type 4).

    Args:
        message: The error text to display to the user.
        ephemeral: If True (default), the message is only visible to
                   the command invoker.  Set False for public errors.

    Returns:
        ``InteractionResponse`` with type 4 and the error content.
    """
    data: dict[str, Any] = {"content": message}
    if ephemeral:
        data["flags"] = int(DiscordMessageFlags.EPHEMERAL)
    return InteractionResponse(
        type=int(DiscordResponseType.CHANNEL_MESSAGE_WITH_SOURCE),
        data=data,
    )


def build_success_response(
    message: str,
    *,
    ephemeral: bool = False,
) -> InteractionResponse:
    """Build a success message response (type 4).

    Args:
        message: The success text to display.
        ephemeral: If True, only the invoker sees the message.
                   Default: False (public).

    Returns:
        ``InteractionResponse`` with type 4 and the success content.
    """
    data: dict[str, Any] = {"content": message}
    if ephemeral:
        data["flags"] = int(DiscordMessageFlags.EPHEMERAL)
    return InteractionResponse(
        type=int(DiscordResponseType.CHANNEL_MESSAGE_WITH_SOURCE),
        data=data,
    )


def build_ack_response() -> InteractionResponse:
    """Build a silent ACK (type 4 with no content) to close a deferred interaction."""
    return InteractionResponse(
        type=int(DiscordResponseType.CHANNEL_MESSAGE_WITH_SOURCE),
        data={"content": ""},
    )


# ═══════════════════════════════════════════════════════════════════════════
# Internal helpers
# ═══════════════════════════════════════════════════════════════════════════


def _extract_user_id(payload: dict[str, Any]) -> str:
    """Extract the Discord user ID from an interaction payload.

    Discord sends user info in different locations depending on context:
    - Guild interactions: ``member.user.id``
    - DM interactions: ``user.id``

    Args:
        payload: Raw Discord interaction payload dict.

    Returns:
        The user's snowflake ID, or ``"unknown"`` if neither path
        yields a valid ID.
    """
    # Guild context — nested under member
    member = payload.get("member")
    if isinstance(member, dict):
        user = member.get("user")
        if isinstance(user, dict) and isinstance(user.get("id"), str):
            return user["id"]

    # DM context — top-level user object
    user = payload.get("user")
    if isinstance(user, dict) and isinstance(user.get("id"), str):
        return user["id"]

    return "unknown"


def _extract_channel_id(payload: dict[str, Any]) -> str:
    """Extract the Discord channel ID from an interaction payload.

    Discord may send ``channel_id`` as a top-level field or nested
    inside a ``channel`` object.

    Args:
        payload: Raw Discord interaction payload dict.

    Returns:
        The channel snowflake ID, or ``""`` if not present.
    """
    channel_id = payload.get("channel_id")
    if isinstance(channel_id, str) and channel_id:
        return channel_id

    channel = payload.get("channel")
    if isinstance(channel, dict):
        cid = channel.get("id")
        if isinstance(cid, str) and cid:
            return cid

    return ""


def _flatten_command_options(
    raw_options: list[Any],
) -> dict[str, Any]:
    """Flatten Discord's recursive option structure into a flat dict.

    Discord slash commands with subcommands (type 1) or subcommand
    groups (type 2) nest options.  This function walks the tree and
    produces a simple ``name → value`` map, discarding intermediate
    subcommand wrappers.

    Args:
        raw_options: The raw ``data.options`` list from the interaction.

    Returns:
        Flat dictionary mapping option names to their resolved values.

    Examples:
        >>> opts = [{"name": "agenda", "type": 3, "value": "Hello"}]
        >>> _flatten_command_options(opts)
        {'agenda': 'Hello'}

        >>> opts = [
        ...     {
        ...         "name": "create",
        ...         "type": 1,
        ...         "options": [{"name": "name", "type": 3, "value": "Foo"}],
        ...     }
        ... ]
        >>> _flatten_command_options(opts)
        {'name': 'Foo'}
    """
    result: dict[str, Any] = {}

    for opt in raw_options:
        if not isinstance(opt, dict):
            continue

        opt_type = opt.get("type")
        nested = opt.get("options")

        # Subcommand (type 1) or subcommand group (type 2) — recurse
        # into nested options, ignoring the intermediate wrapper names.
        if opt_type in (1, 2) and isinstance(nested, list):
            result.update(_flatten_command_options(nested))
        # Regular option — add as leaf
        elif "name" in opt and opt_type not in (1, 2):
            result[opt["name"]] = opt.get("value")

    return result


# ═══════════════════════════════════════════════════════════════════════════
# Exports
# ═══════════════════════════════════════════════════════════════════════════

__all__ = [
    # Types
    "WebhookRequest",
    "WebhookResult",
    "ParsedInteraction",
    "InteractionResponse",
    # Enums
    "DiscordInteractionType",
    "DiscordResponseType",
    "DiscordMessageFlags",
    # Errors
    "WebhookHandlerError",
    "SignatureVerificationError",
    "InvalidPayloadError",
    # Core functions
    "verify_discord_signature",
    "parse_interaction_payload",
    "handle_webhook",
    # Command routing
    "register_command_handler",
    "unregister_command_handler",
    "clear_command_handlers",
    "route_command",
    # Response builders
    "build_deferred_response",
    "build_error_response",
    "build_success_response",
    "build_ack_response",
]
