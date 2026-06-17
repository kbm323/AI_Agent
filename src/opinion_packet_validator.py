"""Opinion packet schema validation for multi-agent meeting rounds.

Sub-AC 5a-1: Defines required fields (persona_id, agenda_item_ref,
opinion_content, confidence, timestamp) for the per-role opinion
packets exchanged during meeting rounds.  Provides a validator
function that inspects packet structure completeness and aggregates
all failures into a structured validation report.

This module mirrors the design of ``qwen_field_validator.py`` so the
Coordinator can treat it as a drop-in validation layer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

# ── ISO-8601 timestamp pattern (tolerant) ──────────────────────────────
# Matches: 2026-06-10T14:30:00Z, 2026-06-10T14:30:00+09:00,
#          2026-06-10T14:30:00.123456Z, 2026-06-10 14:30:00
_ISO8601_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}"
    r"(\.\d+)?(Z|[+-]\d{2}:\d{2})?$"
)

# ── Opinion packet field descriptors ───────────────────────────────────

_OPINION_PACKET_FIELDS: tuple[dict[str, Any], ...] = (
    {
        "name": "persona_id",
        "kind": "kebab_id_string",
        "required": True,
    },
    {
        "name": "agenda_item_ref",
        "kind": "non_empty_string",
        "required": True,
    },
    {
        "name": "opinion_content",
        "kind": "non_empty_string",
        "required": True,
    },
    {
        "name": "confidence",
        "kind": "float_0_1",
        "required": True,
    },
    {
        "name": "timestamp",
        "kind": "iso8601_string",
        "required": True,
    },
)


# ── Data types ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class OpinionFieldValidationError:
    """A single field-level validation failure in an opinion packet.

    Carries enough context for the Coordinator to log, report, or
    auto-correct without re-parsing the raw payload.
    """

    field_name: str
    """Name of the field that failed validation."""

    error_type: str
    """Category: ``missing``, ``wrong_type``, ``invalid_value``,
    ``empty_string``, ``bad_timestamp``."""

    message: str
    """Human-readable description of the failure."""

    expected: str
    """What the validator expected (e.g. ``non-empty str``,
    ``float in [0.0, 1.0]``)."""

    actual: str
    """What was actually received (Python type name or value repr)."""


@dataclass(frozen=True)
class OpinionPacketValidationReport:
    """Aggregated result of validating an opinion packet dict.

    ``passed`` is ``True`` only when **zero** errors were detected.
    The ``errors`` tuple drives downstream logging and recovery.
    """

    passed: bool
    """Overall validation result."""

    errors: tuple[OpinionFieldValidationError, ...]
    """All detected validation failures (empty when passed)."""

    total_fields_checked: int
    """Number of fields that were inspected."""

    @property
    def error_count(self) -> int:
        """Convenience: total number of errors."""
        return len(self.errors)

    def errors_by_field(
        self,
    ) -> dict[str, tuple[OpinionFieldValidationError, ...]]:
        """Group errors by field name for targeted reporting."""
        grouped: dict[str, list[OpinionFieldValidationError]] = {}
        for err in self.errors:
            grouped.setdefault(err.field_name, []).append(err)
        return {k: tuple(v) for k, v in grouped.items()}


# ── Individual field validators ────────────────────────────────────────


def _validate_kebab_id_string(
    name: str, value: Any
) -> list[OpinionFieldValidationError]:
    """Validate a field is a non-empty kebab-case identifier string.

    Kebab-case: lowercase letters, digits, hyphens (e.g.
    ``art-director``, ``marketing-lead``).  No leading/trailing
    hyphens or underscores.
    """
    errors: list[OpinionFieldValidationError] = []

    if value is None:
        errors.append(
            OpinionFieldValidationError(
                field_name=name,
                error_type="missing",
                message=f"'{name}' is None; expected a non-empty kebab-case string.",
                expected="non-empty kebab-case str",
                actual="None",
            )
        )
        return errors

    if not isinstance(value, str):
        errors.append(
            OpinionFieldValidationError(
                field_name=name,
                error_type="wrong_type",
                message=(
                    f"'{name}' must be a string, got {type(value).__name__}"
                ),
                expected="non-empty kebab-case str",
                actual=type(value).__name__,
            )
        )
        return errors

    stripped = value.strip()
    if stripped == "":
        errors.append(
            OpinionFieldValidationError(
                field_name=name,
                error_type="empty_string",
                message=f"'{name}' is empty or whitespace-only.",
                expected="non-empty kebab-case str",
                actual=repr(value)[:80],
            )
        )
        return errors

    # Kebab-case sanity check: only lowercase alphanum + hyphens,
    # no leading/trailing hyphens, no double hyphens.
    if not re.match(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$", stripped):
        errors.append(
            OpinionFieldValidationError(
                field_name=name,
                error_type="invalid_value",
                message=(
                    f"'{name}' = '{stripped}' is not valid kebab-case. "
                    f"Expected pattern: lower-kebab-id (e.g. 'art-director')."
                ),
                expected="kebab-case identifier",
                actual=repr(stripped)[:80],
            )
        )

    return errors


def _validate_non_empty_string(
    name: str, value: Any,
) -> list[OpinionFieldValidationError]:
    """Validate a field is a non-empty string (after stripping)."""
    errors: list[OpinionFieldValidationError] = []

    if value is None:
        errors.append(
            OpinionFieldValidationError(
                field_name=name,
                error_type="missing",
                message=f"'{name}' is None; expected a non-empty string.",
                expected="non-empty str",
                actual="None",
            )
        )
        return errors

    if not isinstance(value, str):
        errors.append(
            OpinionFieldValidationError(
                field_name=name,
                error_type="wrong_type",
                message=(
                    f"'{name}' must be a string, got {type(value).__name__}"
                ),
                expected="non-empty str",
                actual=type(value).__name__,
            )
        )
        return errors

    if value.strip() == "":
        errors.append(
            OpinionFieldValidationError(
                field_name=name,
                error_type="empty_string",
                message=f"'{name}' is empty or whitespace-only.",
                expected="non-empty str",
                actual=repr(value)[:80],
            )
        )

    return errors


def _validate_float_0_1(
    name: str, value: Any,
) -> list[OpinionFieldValidationError]:
    """Validate a field is a float (or int) in [0.0, 1.0].

    Unlike the Qwen validator, confidence is **required** for
    opinion packets — every opinion MUST carry a confidence score.
    """
    errors: list[OpinionFieldValidationError] = []

    if value is None:
        errors.append(
            OpinionFieldValidationError(
                field_name=name,
                error_type="missing",
                message=f"'{name}' is None; expected a float in [0.0, 1.0].",
                expected="float in [0.0, 1.0]",
                actual="None",
            )
        )
        return errors

    if isinstance(value, bool):
        errors.append(
            OpinionFieldValidationError(
                field_name=name,
                error_type="wrong_type",
                message=(
                    f"'{name}' is a boolean ({value}); expected a float. "
                    f"JSON booleans are not valid for this field."
                ),
                expected="float in [0.0, 1.0]",
                actual=f"bool = {value}",
            )
        )
        return errors

    if not isinstance(value, (int, float)):
        errors.append(
            OpinionFieldValidationError(
                field_name=name,
                error_type="wrong_type",
                message=(
                    f"'{name}' must be a float, got {type(value).__name__}"
                ),
                expected="float in [0.0, 1.0]",
                actual=type(value).__name__,
            )
        )
        return errors

    fval = float(value)
    if fval < 0.0 or fval > 1.0:
        errors.append(
            OpinionFieldValidationError(
                field_name=name,
                error_type="invalid_value",
                message=(
                    f"'{name}' = {fval} is outside [0.0, 1.0]"
                ),
                expected="float in [0.0, 1.0]",
                actual=str(fval),
            )
        )

    return errors


def _validate_iso8601_string(
    name: str, value: Any,
) -> list[OpinionFieldValidationError]:
    """Validate a field is an ISO-8601 timestamp string."""
    errors: list[OpinionFieldValidationError] = []

    if value is None:
        errors.append(
            OpinionFieldValidationError(
                field_name=name,
                error_type="missing",
                message=f"'{name}' is None; expected an ISO-8601 timestamp.",
                expected="ISO-8601 str",
                actual="None",
            )
        )
        return errors

    if not isinstance(value, str):
        errors.append(
            OpinionFieldValidationError(
                field_name=name,
                error_type="wrong_type",
                message=(
                    f"'{name}' must be a string, got {type(value).__name__}"
                ),
                expected="ISO-8601 str",
                actual=type(value).__name__,
            )
        )
        return errors

    stripped = value.strip()
    if stripped == "":
        errors.append(
            OpinionFieldValidationError(
                field_name=name,
                error_type="empty_string",
                message=f"'{name}' is empty.",
                expected="ISO-8601 str",
                actual="(empty)",
            )
        )
        return errors

    if not _ISO8601_RE.match(stripped):
        errors.append(
            OpinionFieldValidationError(
                field_name=name,
                error_type="bad_timestamp",
                message=(
                    f"'{name}' = '{stripped}' is not valid ISO-8601. "
                    f"Expected format: YYYY-MM-DDTHH:MM:SS[.fff][Z|±HH:MM]."
                ),
                expected="ISO-8601 timestamp",
                actual=repr(stripped)[:80],
            )
        )

    return errors


# ── Validator dispatch map ─────────────────────────────────────────────

_VALIDATORS: dict[
    str, Callable[[str, Any], list[OpinionFieldValidationError]]
] = {
    "kebab_id_string": _validate_kebab_id_string,
    "non_empty_string": _validate_non_empty_string,
    "float_0_1": _validate_float_0_1,
    "iso8601_string": _validate_iso8601_string,
}


# ── Public API ─────────────────────────────────────────────────────────


def validate_opinion_packet(
    data: dict[str, Any] | None,
) -> OpinionPacketValidationReport:
    """Validate an opinion packet dict against the required field schema.

    This is the main entry point for **Sub-AC 5a-1**.  It accepts a
    dict representing a single role's opinion from a meeting round
    and validates every required field against type and value
    constraints.

    **Required fields:** ``persona_id``, ``agenda_item_ref``,
    ``opinion_content``, ``confidence``, ``timestamp``.

    **Behaviour:**

    * All five fields are checked — no early-exit on first error.
    * ``persona_id`` must be valid kebab-case (e.g. ``art-director``).
    * ``agenda_item_ref`` and ``opinion_content`` must be non-empty strings.
    * ``confidence`` must be a float in [0.0, 1.0] (integers accepted).
    * ``timestamp`` must be ISO-8601 format.
    * ``None`` input is treated as a single fatal error.

    Args:
        data: The opinion packet dict (or ``None``).

    Returns:
        ``OpinionPacketValidationReport`` with ``passed=True`` when
        zero errors are detected.  Inspect ``report.errors`` for
        detailed diagnostics on failure.

    Examples:
        >>> report = validate_opinion_packet({
        ...     "persona_id": "art-director",
        ...     "agenda_item_ref": "visual-identity",
        ...     "opinion_content": "We should adopt a neon-noir palette.",
        ...     "confidence": 0.88,
        ...     "timestamp": "2026-06-10T14:30:00Z",
        ... })
        >>> report.passed
        True

        >>> report = validate_opinion_packet({})
        >>> report.passed
        False
        >>> report.error_count >= 5
        True
    """
    all_errors: list[OpinionFieldValidationError] = []

    # ── Null guard ──
    if data is None:
        return OpinionPacketValidationReport(
            passed=False,
            errors=(
                OpinionFieldValidationError(
                    field_name="<root>",
                    error_type="missing",
                    message="Input data is None — cannot validate.",
                    expected="dict[str, Any]",
                    actual="None",
                ),
            ),
            total_fields_checked=0,
        )

    if not isinstance(data, dict):
        return OpinionPacketValidationReport(
            passed=False,
            errors=(
                OpinionFieldValidationError(
                    field_name="<root>",
                    error_type="wrong_type",
                    message=(
                        f"Input must be a dict, got {type(data).__name__}"
                    ),
                    expected="dict[str, Any]",
                    actual=type(data).__name__,
                ),
            ),
            total_fields_checked=0,
        )

    # ── Validate every declared field ──
    for descriptor in _OPINION_PACKET_FIELDS:
        name: str = descriptor["name"]
        kind: str = descriptor["kind"]
        required: bool = descriptor.get("required", False)

        if name not in data:
            if required:
                all_errors.append(
                    OpinionFieldValidationError(
                        field_name=name,
                        error_type="missing",
                        message=(
                            f"Required field '{name}' is missing from "
                            f"opinion packet."
                        ),
                        expected=kind,
                        actual="(absent)",
                    )
                )
            continue

        value = data[name]
        validator = _VALIDATORS.get(kind)
        if validator is None:
            continue  # defensive: unknown kind

        all_errors.extend(validator(name, value))

    return OpinionPacketValidationReport(
        passed=len(all_errors) == 0,
        errors=tuple(all_errors),
        total_fields_checked=len(_OPINION_PACKET_FIELDS),
    )


# ── Convenience re-exports for tests ───────────────────────────────────

OPINION_PACKET_FIELDS = _OPINION_PACKET_FIELDS
"""Public read-only reference to the field descriptors."""
