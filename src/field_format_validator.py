"""Field format and type validator for meeting system data structures.

Sub-AC 6.1.2: Verifies each field conforms to its expected type, schema,
and format constraints.  Goes beyond presence validation (Sub-AC 6.1.1)
to enforce deep format rules: regex patterns, enum membership, numeric
boundaries, timestamp formats, kebab-case identifiers, and structural
validation of nested objects (e.g. error_log entries).

Design
------
This module validates individual fields or whole dicts against a
declarative schema of field descriptors.  Each descriptor specifies:

* ``name`` — field name
* ``kind`` — validator kind (see :ref:`_VALIDATORS`)
* ``required`` — whether absence is an error
* Additional kind-specific parameters (``enum``, ``min``, ``max``,
  ``pattern``, etc.)

Supported validator kinds
-------------------------
* ``meeting_id`` — regex ``^meeting_\d{8}_[a-f0-9]{12}$``
* ``non_empty_string`` — str with non-whitespace content
* ``enum_string`` — str from a restricted set (case-insensitive)
* ``kebab_id_string`` — lowercase kebab-case (``^[a-z][a-z0-9]*(-[a-z0-9]+)*$``)
* ``string_array`` — list of non-empty strings
* ``string_array_or_empty`` — list of strings, empty list OK
* ``boolean`` — strict True/False
* ``boolean_or_none`` — True/False/None
* ``positive_int`` — int > 0
* ``non_negative_int`` — int >= 0
* ``int_in_range`` — int in [min, max]
* ``float_0_1`` — float or int in [0.0, 1.0]
* ``float_0_1_or_none`` — float in [0.0, 1.0] or None
* ``iso8601_string`` — ISO-8601 timestamp format
* ``iso8601_or_empty`` — ISO-8601 or empty string
* ``list`` — any list
* ``list_of_dicts`` — list where every item is a dict
* ``dict_or_none`` — dict or None
* ``string_or_none`` — str or None
* ``string_or_empty`` — str (may be empty)
* ``path_string`` — valid filesystem path string
* ``version_string`` — semantic-ish version (``name.vN``)

All validation failures are collected — no early-exit on first error.
The report includes a ``passed`` flag for quick branching.

Testable with: malformed types, invalid enums, boundary-value format
inputs, empty arrays, negative numbers, wrong-pattern strings, and
ISO-8601 edge cases.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, FrozenSet

# ── Regex patterns ────────────────────────────────────────────────────

_MEETING_ID_RE = re.compile(r"^meeting_\d{8}_[a-f0-9]{12}$")
"""meeting_id format: meeting_YYYYMMDD_12hexchars"""

_ISO8601_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}"
    r"(\.\d+)?(Z|[+-]\d{2}:\d{2})?$"
)
"""Tolerant ISO-8601: 2026-06-10T14:30:00Z, 2026-06-10 14:30:00, etc."""

_KEBAB_ID_RE = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")
"""Kebab-case: lowercase letters, digits, hyphens; no leading/trailing hyphens."""

_VERSION_RE = re.compile(r"^[a-z][a-z0-9_-]*\.v\d+$")
"""Version string: name.vN (e.g. 'meeting-manifest.v1'). Accepts hyphens and underscores."""

# ── Enum sets ─────────────────────────────────────────────────────────

VALID_STATES: FrozenSet[str] = frozenset({
    "created", "queued", "routing", "context_retrieval",
    "in_meeting", "consensus_building", "validating", "executing",
    "finalizing", "completed", "paused", "deadlocked",
    "escalated", "cancelled", "failed", "stale",
})

VALID_PRIORITIES: FrozenSet[str] = frozenset({"p0", "p1", "p2", "p3"})

VALID_AGENDA_TYPES: FrozenSet[str] = frozenset({
    "creative_production",
    "technical_development",
    "marketing_strategy",
    "risk_assessment",
    "general_planning",
    "project_review",
})

VALID_ROUND_TYPES: FrozenSet[str] = frozenset({
    "opinion",
    "conflict_resolution",
    "convergence",
    "tie_break",
})

VALID_VERDICTS: FrozenSet[str] = frozenset({
    "pass",
    "conditional_pass",
    "revision_required",
    "escalate",
    "fail",
})

# ── Field kind constants ──────────────────────────────────────────────

_ALL = "ALL"  # sentinel for matching any field kind

# ── Data types ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FormatValidationError:
    """A single field-level format/type validation failure.

    Carries enough context for the Coordinator to log, report, or
    auto-correct without re-parsing the raw payload.
    """

    field_name: str
    """Name of the field that failed validation."""

    error_type: str
    """Category: ``missing``, ``wrong_type``, ``invalid_value``,
    ``bad_format``, ``out_of_range``, ``empty_string``,
    ``empty_array``, ``non_string_item``, ``bad_timestamp``,
    ``invalid_entry``."""

    message: str
    """Human-readable description of the failure."""

    expected: str
    """What the validator expected (e.g. ``meeting_YYYYMMDD_hex12``)."""

    actual: str
    """What was actually received (Python type name or value repr)."""


@dataclass(frozen=True)
class FormatValidationReport:
    """Aggregated result of field format validation.

    ``passed`` is ``True`` only when **zero** errors were detected.
    The ``errors`` tuple drives downstream logging and recovery.
    """

    passed: bool
    """Overall validation result."""

    errors: tuple[FormatValidationError, ...]
    """All detected validation failures (empty when passed)."""

    total_fields_checked: int
    """Number of fields that were inspected."""

    schema_version: str = "field-format-validation.v1"
    """Schema version for this validator."""

    @property
    def error_count(self) -> int:
        """Convenience: total number of errors."""
        return len(self.errors)

    def errors_by_field(self) -> dict[str, tuple[FormatValidationError, ...]]:
        """Group errors by field name for targeted reporting."""
        grouped: dict[str, list[FormatValidationError]] = {}
        for err in self.errors:
            grouped.setdefault(err.field_name, []).append(err)
        return {k: tuple(v) for k, v in grouped.items()}


# ── Individual field validators ───────────────────────────────────────


def _validate_meeting_id(
    name: str, value: Any
) -> list[FormatValidationError]:
    """Validate a field matches the meeting_id format.

    Format: ``meeting_YYYYMMDD_12hexchars``
    Example: ``meeting_20260610_5a36918413b1``
    """
    errors: list[FormatValidationError] = []

    if value is None:
        errors.append(
            FormatValidationError(
                field_name=name,
                error_type="missing",
                message=f"'{name}' is None; expected meeting_YYYYMMDD_hex12.",
                expected="meeting_YYYYMMDD_12hexchars",
                actual="None",
            )
        )
        return errors

    if not isinstance(value, str):
        errors.append(
            FormatValidationError(
                field_name=name,
                error_type="wrong_type",
                message=(
                    f"'{name}' must be a string, got {type(value).__name__}"
                ),
                expected="meeting_YYYYMMDD_12hexchars",
                actual=type(value).__name__,
            )
        )
        return errors

    stripped = value.strip()
    if not _MEETING_ID_RE.match(stripped):
        errors.append(
            FormatValidationError(
                field_name=name,
                error_type="bad_format",
                message=(
                    f"'{name}' = '{stripped}' does not match "
                    f"pattern meeting_YYYYMMDD_12hexchars"
                ),
                expected="meeting_YYYYMMDD_12hexchars",
                actual=repr(stripped)[:120],
            )
        )

    return errors


def _validate_non_empty_string(
    name: str, value: Any
) -> list[FormatValidationError]:
    """Validate a field is a non-empty string (after stripping)."""
    errors: list[FormatValidationError] = []

    if value is None:
        errors.append(
            FormatValidationError(
                field_name=name,
                error_type="missing",
                message=f"'{name}' is None; expected a non-empty string.",
                expected="non-empty string",
                actual="None",
            )
        )
        return errors

    if not isinstance(value, str):
        errors.append(
            FormatValidationError(
                field_name=name,
                error_type="wrong_type",
                message=(
                    f"'{name}' must be a string, got {type(value).__name__}"
                ),
                expected="non-empty string",
                actual=type(value).__name__,
            )
        )
        return errors

    if value.strip() == "":
        errors.append(
            FormatValidationError(
                field_name=name,
                error_type="empty_string",
                message=f"'{name}' is empty or whitespace-only.",
                expected="non-empty string",
                actual=repr(value)[:80],
            )
        )

    return errors


def _validate_enum_string(
    name: str, value: Any, enum: FrozenSet[str],
    allow_empty: bool = False,
) -> list[FormatValidationError]:
    """Validate a field is a string from a restricted enum set.

    Matching is case-insensitive after stripping.
    When ``allow_empty`` is True, empty/whitespace-only strings
    are accepted (for pre-routing / pre-validation states).
    """
    errors: list[FormatValidationError] = []

    if value is None:
        errors.append(
            FormatValidationError(
                field_name=name,
                error_type="missing",
                message=(
                    f"'{name}' is None; expected one of {sorted(enum)}"
                    + (" or empty string" if allow_empty else "")
                ),
                expected=f"one of {sorted(enum)}" +
                          (" or empty string" if allow_empty else ""),
                actual="None",
            )
        )
        return errors

    if not isinstance(value, str):
        errors.append(
            FormatValidationError(
                field_name=name,
                error_type="wrong_type",
                message=(
                    f"'{name}' must be a string, got {type(value).__name__}"
                ),
                expected=f"one of {sorted(enum)}",
                actual=repr(value)[:120],
            )
        )
        return errors

    stripped = value.strip().lower()
    if stripped == "" and allow_empty:
        return errors  # empty is acceptable

    matched = False
    for valid in enum:
        if valid.lower() == stripped:
            matched = True
            break

    if not matched:
        valid_list = sorted(enum)
        msg = f"'{name}' value '{value}' is not a recognised value. Valid: {valid_list}"
        if allow_empty:
            msg += " (or empty string)"
        errors.append(
            FormatValidationError(
                field_name=name,
                error_type="invalid_value",
                message=msg,
                expected=f"one of {valid_list}" +
                          (" or empty string" if allow_empty else ""),
                actual=repr(value),
            )
        )

    return errors


def _validate_kebab_id_string(
    name: str, value: Any
) -> list[FormatValidationError]:
    """Validate a field is a kebab-case identifier.

    Pattern: ``^[a-z][a-z0-9]*(-[a-z0-9]+)*$``
    Examples: ``art-director``, ``marketing-lead``
    """
    errors: list[FormatValidationError] = []

    if value is None:
        errors.append(
            FormatValidationError(
                field_name=name,
                error_type="missing",
                message=f"'{name}' is None; expected a kebab-case identifier.",
                expected="kebab-case identifier (e.g. 'art-director')",
                actual="None",
            )
        )
        return errors

    if not isinstance(value, str):
        errors.append(
            FormatValidationError(
                field_name=name,
                error_type="wrong_type",
                message=(
                    f"'{name}' must be a string, got {type(value).__name__}"
                ),
                expected="kebab-case identifier",
                actual=type(value).__name__,
            )
        )
        return errors

    stripped = value.strip()
    if stripped == "":
        errors.append(
            FormatValidationError(
                field_name=name,
                error_type="empty_string",
                message=f"'{name}' is empty or whitespace-only.",
                expected="kebab-case identifier",
                actual=repr(value)[:80],
            )
        )
        return errors

    if not _KEBAB_ID_RE.match(stripped):
        errors.append(
            FormatValidationError(
                field_name=name,
                error_type="bad_format",
                message=(
                    f"'{name}' = '{stripped}' is not valid kebab-case. "
                    f"Expected lowercase letters, digits, hyphens "
                    f"(e.g. 'art-director')."
                ),
                expected="kebab-case identifier",
                actual=repr(stripped)[:80],
            )
        )

    return errors


def _validate_string_array(
    name: str, value: Any, allow_empty: bool = True
) -> list[FormatValidationError]:
    """Validate a field is a list of non-empty strings.

    When ``allow_empty`` is False, an empty list triggers an error.
    Non-string items and empty-string items are always flagged.
    """
    errors: list[FormatValidationError] = []

    if value is None:
        errors.append(
            FormatValidationError(
                field_name=name,
                error_type="missing",
                message=f"'{name}' is None; expected a list of strings.",
                expected="list[str]",
                actual="None",
            )
        )
        return errors

    if not isinstance(value, list):
        errors.append(
            FormatValidationError(
                field_name=name,
                error_type="wrong_type",
                message=(
                    f"'{name}' must be a list, got {type(value).__name__}"
                ),
                expected="list[str]",
                actual=type(value).__name__,
            )
        )
        return errors

    if not allow_empty and len(value) == 0:
        errors.append(
            FormatValidationError(
                field_name=name,
                error_type="empty_array",
                message=f"'{name}' must not be an empty list.",
                expected="non-empty list[str]",
                actual="[] (empty)",
            )
        )
        return errors

    # Check items
    non_string_indices: list[int] = []
    empty_string_indices: list[int] = []
    for i, item in enumerate(value):
        if not isinstance(item, str):
            non_string_indices.append(i)
        elif item.strip() == "":
            empty_string_indices.append(i)

    if non_string_indices:
        errors.append(
            FormatValidationError(
                field_name=name,
                error_type="non_string_item",
                message=(
                    f"'{name}' contains {len(non_string_indices)} non-string "
                    f"item(s) at indices {non_string_indices[:5]}"
                    f"{'…' if len(non_string_indices) > 5 else ''}"
                ),
                expected="list[str] (all items must be strings)",
                actual=(
                    f"list containing "
                    f"{type(value[non_string_indices[0]]).__name__}"
                    f" at index {non_string_indices[0]}"
                ),
            )
        )

    if empty_string_indices:
        errors.append(
            FormatValidationError(
                field_name=name,
                error_type="empty_string",
                message=(
                    f"'{name}' contains {len(empty_string_indices)} "
                    f"empty-string item(s) at indices "
                    f"{empty_string_indices[:5]}"
                    f"{'…' if len(empty_string_indices) > 5 else ''}"
                ),
                expected="list[str] (no empty strings)",
                actual="list containing empty string(s)",
            )
        )

    return errors


def _validate_boolean(
    name: str, value: Any, allow_none: bool = False
) -> list[FormatValidationError]:
    """Validate a field is a strict boolean (True/False).

    Python truthiness rules are deliberately NOT used — 0, 1, "true",
    "false" are all rejected.  When ``allow_none`` is True, None is
    also accepted.
    """
    errors: list[FormatValidationError] = []

    if value is None:
        if allow_none:
            return errors
        errors.append(
            FormatValidationError(
                field_name=name,
                error_type="missing",
                message=f"'{name}' is None; expected a boolean.",
                expected="bool (True or False)",
                actual="None",
            )
        )
        return errors

    if isinstance(value, bool):
        return errors  # strict bool is fine

    # Everything else is wrong_type
    errors.append(
        FormatValidationError(
            field_name=name,
            error_type="wrong_type",
            message=(
                f"'{name}' must be a boolean (True/False), "
                f"got {type(value).__name__} ({repr(value)[:80]})"
            ),
            expected="bool (True or False only)",
            actual=f"{type(value).__name__} = {repr(value)[:80]}",
        )
    )

    return errors


def _validate_positive_int(
    name: str, value: Any
) -> list[FormatValidationError]:
    """Validate a field is a positive integer (>0)."""
    errors: list[FormatValidationError] = []

    if value is None:
        errors.append(
            FormatValidationError(
                field_name=name,
                error_type="missing",
                message=f"'{name}' is None; expected a positive integer.",
                expected="int > 0",
                actual="None",
            )
        )
        return errors

    if isinstance(value, bool):
        errors.append(
            FormatValidationError(
                field_name=name,
                error_type="wrong_type",
                message=(
                    f"'{name}' is a boolean ({value}); expected an integer."
                ),
                expected="int > 0",
                actual=f"bool = {value}",
            )
        )
        return errors

    if isinstance(value, (int, float)):
        if isinstance(value, float) and value != int(value):
            errors.append(
                FormatValidationError(
                    field_name=name,
                    error_type="wrong_type",
                    message=(
                        f"'{name}' is a float ({value}); expected an integer."
                    ),
                    expected="int > 0",
                    actual=f"float = {value}",
                )
            )
            return errors
        ivalue = int(value)
        if ivalue <= 0:
            errors.append(
                FormatValidationError(
                    field_name=name,
                    error_type="out_of_range",
                    message=(
                        f"'{name}' = {ivalue} is not positive. Must be > 0."
                    ),
                    expected="int > 0",
                    actual=str(ivalue),
                )
            )
        return errors

    errors.append(
        FormatValidationError(
            field_name=name,
            error_type="wrong_type",
            message=(
                f"'{name}' must be a positive integer, "
                f"got {type(value).__name__}"
            ),
            expected="int > 0",
            actual=type(value).__name__,
        )
    )
    return errors


def _validate_non_negative_int(
    name: str, value: Any
) -> list[FormatValidationError]:
    """Validate a field is a non-negative integer (>=0)."""
    errors: list[FormatValidationError] = []

    if value is None:
        errors.append(
            FormatValidationError(
                field_name=name,
                error_type="missing",
                message=f"'{name}' is None; expected a non-negative integer.",
                expected="int >= 0",
                actual="None",
            )
        )
        return errors

    if isinstance(value, bool):
        errors.append(
            FormatValidationError(
                field_name=name,
                error_type="wrong_type",
                message=(
                    f"'{name}' is a boolean ({value}); expected an integer."
                ),
                expected="int >= 0",
                actual=f"bool = {value}",
            )
        )
        return errors

    if isinstance(value, (int, float)):
        if isinstance(value, float) and value != int(value):
            errors.append(
                FormatValidationError(
                    field_name=name,
                    error_type="wrong_type",
                    message=(
                        f"'{name}' is a float ({value}); expected an integer."
                    ),
                    expected="int >= 0",
                    actual=f"float = {value}",
                )
            )
            return errors
        ivalue = int(value)
        if ivalue < 0:
            errors.append(
                FormatValidationError(
                    field_name=name,
                    error_type="out_of_range",
                    message=(
                        f"'{name}' = {ivalue} is negative. Must be >= 0."
                    ),
                    expected="int >= 0",
                    actual=str(ivalue),
                )
            )
        return errors

    errors.append(
        FormatValidationError(
            field_name=name,
            error_type="wrong_type",
            message=(
                f"'{name}' must be a non-negative integer, "
                f"got {type(value).__name__}"
            ),
            expected="int >= 0",
            actual=type(value).__name__,
        )
    )
    return errors


def _validate_int_in_range(
    name: str, value: Any, min_val: int, max_val: int
) -> list[FormatValidationError]:
    """Validate a field is an integer in [min_val, max_val]."""
    errors: list[FormatValidationError] = []

    if value is None:
        errors.append(
            FormatValidationError(
                field_name=name,
                error_type="missing",
                message=(
                    f"'{name}' is None; expected an integer "
                    f"in [{min_val}, {max_val}]."
                ),
                expected=f"int in [{min_val}, {max_val}]",
                actual="None",
            )
        )
        return errors

    if isinstance(value, bool):
        errors.append(
            FormatValidationError(
                field_name=name,
                error_type="wrong_type",
                message=(
                    f"'{name}' is a boolean ({value}); expected an integer."
                ),
                expected=f"int in [{min_val}, {max_val}]",
                actual=f"bool = {value}",
            )
        )
        return errors

    if isinstance(value, (int, float)):
        if isinstance(value, float) and value != int(value):
            errors.append(
                FormatValidationError(
                    field_name=name,
                    error_type="wrong_type",
                    message=(
                        f"'{name}' is a float ({value}); expected an integer."
                    ),
                    expected=f"int in [{min_val}, {max_val}]",
                    actual=f"float = {value}",
                )
            )
            return errors
        ivalue = int(value)
        if ivalue < min_val or ivalue > max_val:
            errors.append(
                FormatValidationError(
                    field_name=name,
                    error_type="out_of_range",
                    message=(
                        f"'{name}' = {ivalue} is outside "
                        f"[{min_val}, {max_val}]."
                    ),
                    expected=f"int in [{min_val}, {max_val}]",
                    actual=str(ivalue),
                )
            )
        return errors

    errors.append(
        FormatValidationError(
            field_name=name,
            error_type="wrong_type",
            message=(
                f"'{name}' must be an integer, "
                f"got {type(value).__name__}"
            ),
            expected=f"int in [{min_val}, {max_val}]",
            actual=type(value).__name__,
        )
    )
    return errors


def _validate_float_0_1(
    name: str, value: Any, allow_none: bool = False
) -> list[FormatValidationError]:
    """Validate a field is a float (or int) in [0.0, 1.0]."""
    errors: list[FormatValidationError] = []

    if value is None:
        if allow_none:
            return errors
        errors.append(
            FormatValidationError(
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
            FormatValidationError(
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
            FormatValidationError(
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
            FormatValidationError(
                field_name=name,
                error_type="out_of_range",
                message=(
                    f"'{name}' = {fval} is outside [0.0, 1.0]"
                ),
                expected="float in [0.0, 1.0]",
                actual=str(fval),
            )
        )

    return errors


def _validate_iso8601_string(
    name: str, value: Any, allow_empty: bool = False
) -> list[FormatValidationError]:
    """Validate a field is an ISO-8601 timestamp string.

    When ``allow_empty`` is True, an empty string is also accepted.
    """
    errors: list[FormatValidationError] = []

    if value is None:
        errors.append(
            FormatValidationError(
                field_name=name,
                error_type="missing",
                message=f"'{name}' is None; expected an ISO-8601 timestamp.",
                expected="ISO-8601 timestamp",
                actual="None",
            )
        )
        return errors

    if not isinstance(value, str):
        errors.append(
            FormatValidationError(
                field_name=name,
                error_type="wrong_type",
                message=(
                    f"'{name}' must be a string, got {type(value).__name__}"
                ),
                expected="ISO-8601 timestamp",
                actual=type(value).__name__,
            )
        )
        return errors

    stripped = value.strip()
    if stripped == "":
        if allow_empty:
            return errors
        errors.append(
            FormatValidationError(
                field_name=name,
                error_type="empty_string",
                message=f"'{name}' is empty.",
                expected="ISO-8601 timestamp",
                actual="(empty)",
            )
        )
        return errors

    if not _ISO8601_RE.match(stripped):
        errors.append(
            FormatValidationError(
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


def _validate_list(
    name: str, value: Any
) -> list[FormatValidationError]:
    """Validate a field is a list (any content)."""
    errors: list[FormatValidationError] = []

    if value is None:
        errors.append(
            FormatValidationError(
                field_name=name,
                error_type="missing",
                message=f"'{name}' is None; expected a list.",
                expected="list",
                actual="None",
            )
        )
        return errors

    if not isinstance(value, list):
        errors.append(
            FormatValidationError(
                field_name=name,
                error_type="wrong_type",
                message=(
                    f"'{name}' must be a list, got {type(value).__name__}"
                ),
                expected="list",
                actual=type(value).__name__,
            )
        )

    return errors


def _validate_list_of_dicts(
    name: str, value: Any
) -> list[FormatValidationError]:
    """Validate a field is a list where every item is a dict.

    Used for structured arrays like ``error_log``.
    """
    errors: list[FormatValidationError] = []

    if value is None:
        errors.append(
            FormatValidationError(
                field_name=name,
                error_type="missing",
                message=f"'{name}' is None; expected a list of dicts.",
                expected="list[dict]",
                actual="None",
            )
        )
        return errors

    if not isinstance(value, list):
        errors.append(
            FormatValidationError(
                field_name=name,
                error_type="wrong_type",
                message=(
                    f"'{name}' must be a list, got {type(value).__name__}"
                ),
                expected="list[dict]",
                actual=type(value).__name__,
            )
        )
        return errors

    non_dict_indices: list[int] = []
    for i, item in enumerate(value):
        if not isinstance(item, dict):
            non_dict_indices.append(i)

    if non_dict_indices:
        errors.append(
            FormatValidationError(
                field_name=name,
                error_type="invalid_entry",
                message=(
                    f"'{name}' contains {len(non_dict_indices)} non-dict "
                    f"item(s) at indices {non_dict_indices[:5]}"
                    f"{'…' if len(non_dict_indices) > 5 else ''}"
                ),
                expected="list[dict] (all items must be dicts)",
                actual=(
                    f"list containing "
                    f"{type(value[non_dict_indices[0]]).__name__}"
                    f" at index {non_dict_indices[0]}"
                ),
            )
        )

    return errors


def _validate_dict_or_none(
    name: str, value: Any
) -> list[FormatValidationError]:
    """Validate a field is a dict or None."""
    errors: list[FormatValidationError] = []

    if value is None:
        return errors

    if not isinstance(value, dict):
        errors.append(
            FormatValidationError(
                field_name=name,
                error_type="wrong_type",
                message=(
                    f"'{name}' must be a dict or None, "
                    f"got {type(value).__name__}"
                ),
                expected="dict or None",
                actual=type(value).__name__,
            )
        )

    return errors


def _validate_string_or_none(
    name: str, value: Any
) -> list[FormatValidationError]:
    """Validate a field is a string or None."""
    errors: list[FormatValidationError] = []

    if value is None:
        return errors

    if not isinstance(value, str):
        errors.append(
            FormatValidationError(
                field_name=name,
                error_type="wrong_type",
                message=(
                    f"'{name}' must be a string or None, "
                    f"got {type(value).__name__}"
                ),
                expected="str or None",
                actual=type(value).__name__,
            )
        )

    return errors


def _validate_string_or_empty(
    name: str, value: Any
) -> list[FormatValidationError]:
    """Validate a field is a string (may be empty)."""
    errors: list[FormatValidationError] = []

    if value is None:
        errors.append(
            FormatValidationError(
                field_name=name,
                error_type="missing",
                message=f"'{name}' is None; expected a string.",
                expected="str",
                actual="None",
            )
        )
        return errors

    if not isinstance(value, str):
        errors.append(
            FormatValidationError(
                field_name=name,
                error_type="wrong_type",
                message=(
                    f"'{name}' must be a string, got {type(value).__name__}"
                ),
                expected="str",
                actual=type(value).__name__,
            )
        )

    return errors


def _validate_path_string(
    name: str, value: Any
) -> list[FormatValidationError]:
    """Validate a field looks like a filesystem path string."""
    errors: list[FormatValidationError] = []

    if value is None:
        errors.append(
            FormatValidationError(
                field_name=name,
                error_type="missing",
                message=f"'{name}' is None; expected a path string.",
                expected="path string",
                actual="None",
            )
        )
        return errors

    if not isinstance(value, str):
        errors.append(
            FormatValidationError(
                field_name=name,
                error_type="wrong_type",
                message=(
                    f"'{name}' must be a string, got {type(value).__name__}"
                ),
                expected="path string",
                actual=type(value).__name__,
            )
        )
        return errors

    stripped = value.strip()
    if stripped == "":
        errors.append(
            FormatValidationError(
                field_name=name,
                error_type="empty_string",
                message=f"'{name}' is empty or whitespace-only.",
                expected="path string",
                actual=repr(value)[:80],
            )
        )

    return errors


def _validate_version_string(
    name: str, value: Any
) -> list[FormatValidationError]:
    """Validate a field matches the version string format (name.vN)."""
    errors: list[FormatValidationError] = []

    if value is None:
        errors.append(
            FormatValidationError(
                field_name=name,
                error_type="missing",
                message=f"'{name}' is None; expected a version string.",
                expected="name.vN (e.g. 'meeting-manifest.v1')",
                actual="None",
            )
        )
        return errors

    if not isinstance(value, str):
        errors.append(
            FormatValidationError(
                field_name=name,
                error_type="wrong_type",
                message=(
                    f"'{name}' must be a string, got {type(value).__name__}"
                ),
                expected="name.vN",
                actual=type(value).__name__,
            )
        )
        return errors

    stripped = value.strip()
    if not _VERSION_RE.match(stripped):
        errors.append(
            FormatValidationError(
                field_name=name,
                error_type="bad_format",
                message=(
                    f"'{name}' = '{stripped}' does not match "
                    f"version format (name.vN)."
                ),
                expected="name.vN (e.g. 'meeting-manifest.v1')",
                actual=repr(stripped)[:80],
            )
        )

    return errors


# ── Validator dispatch map ────────────────────────────────────────────

_VALIDATORS: dict[str, Callable[..., list[FormatValidationError]]] = {
    "meeting_id": _validate_meeting_id,
    "non_empty_string": _validate_non_empty_string,
    "kebab_id_string": _validate_kebab_id_string,
    "string_array": lambda n, v: _validate_string_array(n, v, allow_empty=True),
    "string_array_non_empty": lambda n, v: _validate_string_array(n, v, allow_empty=False),
    "boolean": lambda n, v: _validate_boolean(n, v, allow_none=False),
    "boolean_or_none": lambda n, v: _validate_boolean(n, v, allow_none=True),
    "positive_int": _validate_positive_int,
    "non_negative_int": _validate_non_negative_int,
    "float_0_1": lambda n, v: _validate_float_0_1(n, v, allow_none=False),
    "float_0_1_or_none": lambda n, v: _validate_float_0_1(n, v, allow_none=True),
    "iso8601_string": lambda n, v: _validate_iso8601_string(n, v, allow_empty=False),
    "iso8601_or_empty": lambda n, v: _validate_iso8601_string(n, v, allow_empty=True),
    "list": _validate_list,
    "list_of_dicts": _validate_list_of_dicts,
    "dict_or_none": _validate_dict_or_none,
    "string_or_none": _validate_string_or_none,
    "string_or_empty": _validate_string_or_empty,
    "path_string": _validate_path_string,
    "version_string": _validate_version_string,
}


# ── Resolver for enum-parameterised validators ────────────────────────

def _resolve_validator(
    kind: str, descriptor: dict[str, Any]
) -> Callable[..., list[FormatValidationError]] | None:
    """Resolve a validator callable for a given field kind and descriptor."""
    if kind == "enum_string":
        enum = descriptor.get("enum", frozenset())
        allow_empty = descriptor.get("allow_empty", False)
        return lambda n, v: _validate_enum_string(n, v, enum, allow_empty=allow_empty)
    if kind == "enum_string_or_empty":
        enum = descriptor.get("enum", frozenset())
        return lambda n, v: _validate_enum_string(n, v, enum, allow_empty=True)
    if kind == "int_in_range":
        min_val = descriptor.get("min", 0)
        max_val = descriptor.get("max", 0)
        return lambda n, v: _validate_int_in_range(n, v, min_val, max_val)
    return _VALIDATORS.get(kind)


# ── Public API ────────────────────────────────────────────────────────


def validate_field(
    name: str,
    value: Any,
    kind: str,
    *,
    required: bool = True,
    **kwargs: Any,
) -> list[FormatValidationError]:
    """Validate a single field's type and format.

    Args:
        name: Field name for error messages.
        value: The value to validate.
        kind: Validator kind (e.g. ``'meeting_id'``, ``'enum_string'``).
        required: Whether absence is an error.  Individual validators
                  that accept None (e.g. ``'string_or_none'``) override
                  this — they will accept None even when ``required=True``
                  because None is a valid value for those kinds.
        **kwargs: Additional parameters for the validator kind
                  (e.g. ``enum`` for ``'enum_string'``).

    Returns:
        List of validation errors (empty when valid).

    Examples:
        >>> errors = validate_field("priority", "p2", "enum_string", enum=VALID_PRIORITIES)
        >>> len(errors)
        0

        >>> errors = validate_field("priority", "p5", "enum_string", enum=VALID_PRIORITIES)
        >>> len(errors) >= 1
        True
        >>> errors[0].error_type
        'invalid_value'
    """
    descriptor: dict[str, Any] = {"name": name, "kind": kind, **kwargs}

    # Let individual validators decide how to handle None.
    # The required flag is primarily used by validate_dict_fields
    # for missing-key detection; at this level we trust the validator.
    if value is None:
        # _or_none validators accept None; others will report missing/wrong_type
        pass

    validator = _resolve_validator(kind, descriptor)
    if validator is None:
        # Unknown kind — skip gracefully
        return []

    return validator(name, value)


def validate_dict_fields(
    data: dict[str, Any],
    field_descriptors: tuple[dict[str, Any], ...],
) -> FormatValidationReport:
    """Validate multiple fields in a dict against declared descriptors.

    Args:
        data: The dict to validate.
        field_descriptors: Tuple of field descriptor dicts, each with
                           ``name``, ``kind``, ``required``, and optional
                           kind-specific parameters (``enum``, ``min``,
                           ``max``).

    Returns:
        ``FormatValidationReport`` with aggregated results.

    Examples:
        >>> from src.field_format_validator import (
        ...     validate_dict_fields, VALID_PRIORITIES,
        ... )
        >>> fields = (
        ...     {"name": "priority", "kind": "enum_string",
        ...      "enum": VALID_PRIORITIES, "required": True},
        ...     {"name": "round_count", "kind": "int_in_range",
        ...      "min": 0, "max": 4, "required": True},
        ... )
        >>> report = validate_dict_fields(
        ...     {"priority": "p2", "round_count": 1}, fields
        ... )
        >>> report.passed
        True
    """
    all_errors: list[FormatValidationError] = []

    # ── Null guard ──
    if data is None:
        return FormatValidationReport(
            passed=False,
            errors=(
                FormatValidationError(
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
        return FormatValidationReport(
            passed=False,
            errors=(
                FormatValidationError(
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
    for descriptor in field_descriptors:
        name: str = descriptor["name"]
        kind: str = descriptor["kind"]
        required: bool = descriptor.get("required", True)

        if name not in data:
            if required:
                all_errors.append(
                    FormatValidationError(
                        field_name=name,
                        error_type="missing",
                        message=(
                            f"Required field '{name}' is missing."
                        ),
                        expected=kind,
                        actual="(absent)",
                    )
                )
            continue

        value = data[name]
        validator = _resolve_validator(kind, descriptor)
        if validator is None:
            continue

        all_errors.extend(validator(name, value))

    return FormatValidationReport(
        passed=len(all_errors) == 0,
        errors=tuple(all_errors),
        total_fields_checked=len(field_descriptors),
    )


# ── Pre-built field descriptor sets ───────────────────────────────────

MANIFEST_CORE_FIELDS: tuple[dict[str, Any], ...] = (
    {"name": "meeting_id", "kind": "meeting_id", "required": True},
    {"name": "state", "kind": "enum_string", "enum": VALID_STATES,
     "required": True},
    {"name": "priority", "kind": "enum_string", "enum": VALID_PRIORITIES,
     "required": True},
    {"name": "agenda", "kind": "non_empty_string", "required": True},
    {"name": "agenda_type", "kind": "enum_string_or_empty",
     "enum": VALID_AGENDA_TYPES, "required": True},
    {"name": "tags", "kind": "string_array", "required": True},
    {"name": "risk_tags", "kind": "string_array", "required": True},
    {"name": "required_roles", "kind": "string_array", "required": True},
    {"name": "optional_roles", "kind": "string_array", "required": True},
    {"name": "round_count", "kind": "int_in_range", "min": 0, "max": 4,
     "required": True},
    {"name": "validation_score", "kind": "float_0_1", "required": True},
    {"name": "validation_verdict", "kind": "enum_string_or_empty",
     "enum": VALID_VERDICTS, "required": True},
    {"name": "validator_required", "kind": "boolean", "required": True},
    {"name": "codex_required", "kind": "boolean", "required": True},
    {"name": "consensus", "kind": "string_or_empty", "required": True},
    {"name": "user_id", "kind": "non_empty_string", "required": True},
    {"name": "channel_id", "kind": "non_empty_string", "required": True},
)

MANIFEST_SYSTEM_FIELDS: tuple[dict[str, Any], ...] = (
    {"name": "error_log", "kind": "list_of_dicts", "required": True},
    {"name": "manifest_path", "kind": "path_string", "required": True},
    {"name": "created_at", "kind": "iso8601_string", "required": True},
    {"name": "updated_at", "kind": "iso8601_string", "required": True},
    {"name": "schema_version", "kind": "version_string", "required": True},
    {"name": "max_rounds", "kind": "positive_int", "required": True},
    {"name": "max_agents_per_meeting", "kind": "positive_int",
     "required": True},
    {"name": "token_limit_worker", "kind": "positive_int", "required": True},
    {"name": "token_limit_validator", "kind": "positive_int",
     "required": True},
    {"name": "token_limit_codex", "kind": "positive_int", "required": True},
    {"name": "primary_validator_model", "kind": "non_empty_string",
     "required": True},
    {"name": "conditional_validator_model", "kind": "string_or_empty",
     "required": True},
)

MANIFEST_OPTIONAL_FIELDS: tuple[dict[str, Any], ...] = (
    {"name": "thread_id", "kind": "string_or_empty", "required": False},
    {"name": "guild_id", "kind": "string_or_empty", "required": False},
    {"name": "completed_step", "kind": "string_or_none", "required": False},
    {"name": "meetings_root", "kind": "string_or_empty", "required": False},
    {"name": "max_concurrent_meetings", "kind": "positive_int",
     "required": False},
    {"name": "max_concurrent_opencode_calls", "kind": "positive_int",
     "required": False},
)

ALL_MANIFEST_FIELDS: tuple[dict[str, Any], ...] = (
    MANIFEST_CORE_FIELDS + MANIFEST_SYSTEM_FIELDS + MANIFEST_OPTIONAL_FIELDS
)

CONTEXT_PACKET_FORMAT_FIELDS: tuple[dict[str, Any], ...] = (
    {"name": "meeting_id", "kind": "meeting_id", "required": True},
    {"name": "role_id", "kind": "kebab_id_string", "required": True},
    {"name": "state", "kind": "enum_string", "enum": VALID_STATES,
     "required": True},
    {"name": "priority", "kind": "enum_string", "enum": VALID_PRIORITIES,
     "required": True},
    {"name": "agenda", "kind": "non_empty_string", "required": True},
    {"name": "agenda_type", "kind": "enum_string_or_empty",
     "enum": VALID_AGENDA_TYPES, "required": True},
    {"name": "tags", "kind": "string_array", "required": True},
    {"name": "risk_tags", "kind": "string_array", "required": True},
    {"name": "round", "kind": "positive_int", "required": True},
    {"name": "round_type", "kind": "enum_string",
     "enum": VALID_ROUND_TYPES, "required": True},
    {"name": "round_context", "kind": "non_empty_string", "required": True},
    {"name": "token_budget", "kind": "positive_int", "required": True},
    {"name": "previous_rounds", "kind": "list", "required": True},
)

# ── Convenience entry points ──────────────────────────────────────────


def validate_manifest_fields(
    data: dict[str, Any],
) -> FormatValidationReport:
    """Validate a complete meeting manifest's field types and formats.

    Validates all core, system, and optional manifest fields against
    their expected types, enums, patterns, and value ranges.

    Args:
        data: Parsed manifest dict.

    Returns:
        ``FormatValidationReport`` with aggregated results.
    """
    return validate_dict_fields(data, ALL_MANIFEST_FIELDS)


def validate_context_packet_format(
    data: dict[str, Any],
) -> FormatValidationReport:
    """Validate a context packet's field types and formats.

    Goes beyond presence validation (Sub-AC 6.1.1) to enforce deep
    format rules: meeting_id pattern, kebab-case role_id, enum
    membership, numeric boundaries, etc.

    Args:
        data: Parsed context packet dict.

    Returns:
        ``FormatValidationReport`` with aggregated results.
    """
    return validate_dict_fields(data, CONTEXT_PACKET_FORMAT_FIELDS)


# ── Exports ───────────────────────────────────────────────────────────

__all__ = [
    "FormatValidationError",
    "FormatValidationReport",
    "VALID_STATES",
    "VALID_PRIORITIES",
    "VALID_AGENDA_TYPES",
    "VALID_ROUND_TYPES",
    "VALID_VERDICTS",
    "MANIFEST_CORE_FIELDS",
    "MANIFEST_SYSTEM_FIELDS",
    "MANIFEST_OPTIONAL_FIELDS",
    "ALL_MANIFEST_FIELDS",
    "CONTEXT_PACKET_FORMAT_FIELDS",
    "validate_field",
    "validate_dict_fields",
    "validate_manifest_fields",
    "validate_context_packet_format",
]
