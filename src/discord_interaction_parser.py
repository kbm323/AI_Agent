"""Discord Interaction Payload Parser — Sub-AC 1b-i

Receives a raw Discord interaction JSON payload, verifies the interaction
type is APPLICATION_COMMAND (type 2) and that the command name matches the
expected name, then extracts raw option values into a flat name→value dict.

This module is the focused, testable parser layer sitting between the raw
Discord HTTP/webhook ingress and the Coordinator command pipeline.  Unlike
the broader ``discord_webhook_handler.py`` (Sub-AC 1.1b) which handles
Ed25519 signature verification and full HTTP lifecycle, this module is
concerned **only** with payload structure validation and option extraction.

Design decisions
----------------
- Stateless: every call is self-contained — no registry, no side effects.
- Returns a structured ``InteractionParseResult`` for every input — never
  raises exceptions for invalid payloads (callers branch on ``.success``).
- Option flattening follows Discord's recursive subcommand nesting rules
  (type 1 = SUB_COMMAND, type 2 = SUB_COMMAND_GROUP).
- Expected command name is optional — when provided, a mismatch produces
  a structured error rather than an exception.

Testability
-----------
All validation logic is exercised with mock Discord interaction JSON
payloads.  No network, no signing keys, no Ed25519 required.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


# ── Discord Interaction Type ────────────────────────────────────────────

DISCORD_INTERACTION_TYPE_PING = 1
"""Discord PING interaction type (endpoint verification)."""

DISCORD_INTERACTION_TYPE_APPLICATION_COMMAND = 2
"""Discord APPLICATION_COMMAND interaction type (slash command)."""

_VALID_INTERACTION_TYPES: frozenset[int] = frozenset({
    DISCORD_INTERACTION_TYPE_PING,
    DISCORD_INTERACTION_TYPE_APPLICATION_COMMAND,
})


# ── Discord Command Option Types ─────────────────────────────────────────

DISCORD_OPTION_TYPE_SUB_COMMAND = 1
DISCORD_OPTION_TYPE_SUB_COMMAND_GROUP = 2

# Option types that carry leaf values (i.e. not structural containers).
_LEAF_OPTION_TYPES: frozenset[int] = frozenset({3, 4, 5, 6, 7, 8, 9, 10, 11})

# Human-readable option type labels (for error messages).
_OPTION_TYPE_LABELS: dict[int, str] = {
    1: "SUB_COMMAND",
    2: "SUB_COMMAND_GROUP",
    3: "STRING",
    4: "INTEGER",
    5: "BOOLEAN",
    6: "USER",
    7: "CHANNEL",
    8: "ROLE",
    9: "MENTIONABLE",
    10: "NUMBER",
    11: "ATTACHMENT",
}


# ── Output data types ────────────────────────────────────────────────────

@dataclass(frozen=True)
class ParsedCommandOptions:
    """Successfully parsed command options from an APPLICATION_COMMAND payload.

    All option values are extracted in their raw form as delivered by
    Discord — no type coercion or normalization is performed here.
    """

    command_name: str
    """The Discord slash command name (e.g. ``"meeting"``)."""

    options: dict[str, Any] = field(default_factory=dict)
    """Flattened option name → raw value map.

    Values are the raw Python values decoded from JSON: ``str``, ``int``,
    ``float``, ``bool``, or ``None`` for options with no value.
    """

    raw_payload: dict[str, Any] = field(default_factory=dict)
    """The original, unmodified interaction JSON payload (for debugging)."""


@dataclass(frozen=True)
class InteractionParseResult:
    """The result of parsing and validating a Discord interaction payload.

    Pattern: every call to :func:`parse_interaction_command` returns
    exactly one ``InteractionParseResult``.  Callers inspect ``.success``
    and branch accordingly.

    When ``success`` is True:
      - ``.parsed`` carries the extracted command name and options.

    When ``success`` is False:
      - ``.error`` holds a human-readable error message.
      - ``.error_code`` holds a machine-readable error code.
    """

    success: bool
    """True when the interaction payload was successfully parsed and
    validated according to all specified constraints."""

    parsed: ParsedCommandOptions | None = None
    """The extracted command options (only populated when success=True)."""

    error: str = ""
    """Human-readable error description (only populated when success=False)."""

    error_code: str = ""
    """Machine-readable error code for programmatic handling."""


# ── Public API ───────────────────────────────────────────────────────────

def parse_interaction_command(
    payload: dict[str, Any] | str | bytes,
    *,
    expected_command_name: str | None = None,
) -> InteractionParseResult:
    """Parse a Discord interaction payload and extract slash command options.

    This is the single entry point for Sub-AC 1b-i.  It receives a raw
    interaction payload (as a dict, JSON string, or raw bytes), validates
    the structural envelope, verifies the interaction type is
    APPLICATION_COMMAND, optionally checks that the command name matches
    an expected value, and extracts option values into a flat name→value
    dict.

    Args:
        payload: Raw Discord interaction payload.  May be:
            - A ``dict`` (already parsed JSON).
            - A ``str`` (JSON string).
            - ``bytes`` (UTF-8 encoded JSON).
        expected_command_name: If provided, the interaction's command
            name MUST match this value.  Mismatch produces a failed
            result.  Defaults to ``None`` (any command name is accepted).

    Returns:
        ``InteractionParseResult`` — inspect ``.success``.

    Examples:
        >>> payload = {
        ...     "id": "abc", "token": "tok", "type": 2,
        ...     "data": {"name": "meeting", "type": 1,
        ...              "options": [{"name": "agenda", "type": 3,
        ...                           "value": "Q2 Roadmap"}]}
        ... }
        >>> result = parse_interaction_command(
        ...     payload,
        ...     expected_command_name="meeting",
        ... )
        >>> result.success
        True
        >>> result.parsed.command_name
        'meeting'
        >>> result.parsed.options["agenda"]
        'Q2 Roadmap'
    """
    # ── Step 1: Normalize input to dict ────────────────────────────
    parsed_dict: dict[str, Any] | InteractionParseResult
    if isinstance(payload, bytes):
        try:
            payload_str = payload.decode("utf-8")
        except UnicodeDecodeError:
            return InteractionParseResult(
                success=False,
                error="Raw payload is not valid UTF-8",
                error_code="INVALID_UTF8",
            )
        parsed_dict = _parse_json_string(payload_str)
    elif isinstance(payload, str):
        parsed_dict = _parse_json_string(payload)
    else:
        parsed_dict = payload

    if isinstance(parsed_dict, InteractionParseResult):
        return parsed_dict
    payload = parsed_dict

    if not isinstance(payload, dict):
        return InteractionParseResult(
            success=False,
            error="Interaction payload is not a JSON object",
            error_code="NOT_A_DICT",
        )

    # ── Step 2: Validate envelope fields ───────────────────────────
    envelope_error = _validate_envelope(payload)
    if envelope_error:
        return envelope_error

    # ── Step 3: Check interaction type ─────────────────────────────
    interaction_type = payload["type"]

    if interaction_type == DISCORD_INTERACTION_TYPE_PING:
        return InteractionParseResult(
            success=False,
            error="PING interactions are not slash commands",
            error_code="NOT_APPLICATION_COMMAND",
        )

    if interaction_type != DISCORD_INTERACTION_TYPE_APPLICATION_COMMAND:
        return InteractionParseResult(
            success=False,
            error=f"Expected APPLICATION_COMMAND (type 2), got type {interaction_type}",
            error_code="WRONG_INTERACTION_TYPE",
        )

    # ── Step 4: Validate data object ───────────────────────────────
    data = payload.get("data")
    if not isinstance(data, dict):
        return InteractionParseResult(
            success=False,
            error="APPLICATION_COMMAND interaction missing valid 'data' object",
            error_code="MISSING_DATA",
        )

    command_name = data.get("name", "")
    if not isinstance(command_name, str) or not command_name:
        return InteractionParseResult(
            success=False,
            error="APPLICATION_COMMAND data missing valid 'name'",
            error_code="MISSING_COMMAND_NAME",
        )

    # ── Step 5: Verify expected command name ───────────────────────
    if expected_command_name is not None and command_name != expected_command_name:
        return InteractionParseResult(
            success=False,
            error=(
                f"Command name mismatch: expected '/{expected_command_name}', "
                f"got '/{command_name}'"
            ),
            error_code="COMMAND_NAME_MISMATCH",
        )

    # ── Step 6: Extract and flatten options ────────────────────────
    raw_options = data.get("options", [])
    if not isinstance(raw_options, list):
        raw_options = []

    options = _flatten_options(raw_options)

    return InteractionParseResult(
        success=True,
        parsed=ParsedCommandOptions(
            command_name=command_name,
            options=options,
            raw_payload=payload,
        ),
    )


def parse_interaction_command_from_bytes(
    raw_body: bytes,
    *,
    expected_command_name: str | None = None,
) -> InteractionParseResult:
    """Convenience wrapper: parse from raw HTTP body bytes.

    Equivalent to :func:`parse_interaction_command` with a ``bytes``
    payload.  Provided as a separate entry point so callers from HTTP
    handlers have a clear API surface.

    Args:
        raw_body: Raw HTTP request body bytes (UTF-8 JSON).
        expected_command_name: Optional expected command name.

    Returns:
        ``InteractionParseResult`` — inspect ``.success``.
    """
    return parse_interaction_command(
        raw_body,
        expected_command_name=expected_command_name,
    )


# ── Internal: JSON parsing ──────────────────────────────────────────────

def _parse_json_string(raw: str) -> dict[str, Any] | InteractionParseResult:
    """Parse a JSON string into a dict, returning an error result on failure."""
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        return InteractionParseResult(
            success=False,
            error=f"Failed to parse interaction JSON body: {exc}",
            error_code="INVALID_JSON",
        )
    if not isinstance(result, dict):
        return InteractionParseResult(
            success=False,
            error="Interaction payload is not a JSON object",
            error_code="NOT_A_DICT",
        )
    return result


# ── Internal: envelope validation ───────────────────────────────────────

def _validate_envelope(payload: dict[str, Any]) -> InteractionParseResult | None:
    """Validate the required envelope fields (id, token, type).

    Returns None when all checks pass, otherwise an error result.
    """
    # id field
    payload_id = payload.get("id")
    if not isinstance(payload_id, str) or not payload_id:
        return InteractionParseResult(
            success=False,
            error="Interaction payload missing valid 'id'",
            error_code="MISSING_ID",
        )

    # token field
    payload_token = payload.get("token")
    if not isinstance(payload_token, str) or not payload_token:
        return InteractionParseResult(
            success=False,
            error="Interaction payload missing valid 'token'",
            error_code="MISSING_TOKEN",
        )

    # type field
    raw_type = payload.get("type")
    if not isinstance(raw_type, int):
        return InteractionParseResult(
            success=False,
            error="Interaction payload missing or invalid 'type' (must be an integer)",
            error_code="MISSING_TYPE",
        )

    if raw_type not in _VALID_INTERACTION_TYPES:
        return InteractionParseResult(
            success=False,
            error=f"Unknown interaction type: {raw_type}",
            error_code="UNKNOWN_TYPE",
        )

    return None


# ── Internal: option flattening ─────────────────────────────────────────

def _flatten_options(
    raw_options: list[Any],
    *,
    _depth: int = 0,
) -> dict[str, Any]:
    """Flatten Discord's recursive option structure into a flat name→value dict.

    Walks subcommands (type 1) and subcommand groups (type 2), discarding
    intermediate wrapper names and collecting only leaf-value options.

    Args:
        raw_options: The raw ``data.options`` list from the interaction.
        _depth: Internal recursion guard (max depth 4).

    Returns:
        Flat dictionary mapping option names to their raw values.
    """
    if _depth > 4:
        # Safety guard — Discord limits nesting to 2 levels, but we
        # cap at 4 to prevent infinite recursion on malformed payloads.
        return {}

    result: dict[str, Any] = {}

    for opt in raw_options:
        if not isinstance(opt, dict):
            continue

        opt_type = opt.get("type")
        nested = opt.get("options")

        # Subcommand (type 1) or subcommand group (type 2) — recurse
        # into nested options, discarding intermediate wrapper names.
        if opt_type in (DISCORD_OPTION_TYPE_SUB_COMMAND, DISCORD_OPTION_TYPE_SUB_COMMAND_GROUP) and isinstance(nested, list):
            result.update(_flatten_options(nested, _depth=_depth + 1))
            continue

        # Leaf option — add to result
        opt_name = opt.get("name")
        if isinstance(opt_name, str) and opt_name:
            result[opt_name] = opt.get("value")

    return result


# ── Exports ──────────────────────────────────────────────────────────────

__all__ = [
    # Constants
    "DISCORD_INTERACTION_TYPE_PING",
    "DISCORD_INTERACTION_TYPE_APPLICATION_COMMAND",
    "DISCORD_OPTION_TYPE_SUB_COMMAND",
    "DISCORD_OPTION_TYPE_SUB_COMMAND_GROUP",
    # Data types
    "ParsedCommandOptions",
    "InteractionParseResult",
    # Public API
    "parse_interaction_command",
    "parse_interaction_command_from_bytes",
    # Internal (exposed for testing)
    "_flatten_options",
    "_validate_envelope",
]
