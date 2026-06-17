"""Context packet required-fields presence validator.

Sub-AC 6.1.1: Validates that all mandatory fields exist in a context
packet regardless of their content.  Accepts a dict parsed from the
JSON context packet (Coordinator → opencode-go worker), checks every
declared required field for presence, and returns a structured
validation report.

**Design**

- All required fields are checked — no early-exit on first missing field.
- Empty / missing fields are reported with ``error_type="missing"``.
- ``None`` and non-dict inputs are handled gracefully.
- Optional fields (``previous_rounds_data``, ``knowledge_context``,
  ``evidence_summary``, ``rebuttal_points``, ``unresolved_issues``)
  are validated when present but their absence does NOT cause failure.
- The report includes a ``passed`` flag for quick branching.

**Testable with**
- Complete valid packets
- Packets missing one or more required fields
- Empty packets (``{}``)
- ``None`` and non-dict input
- Packets where required fields have wrong types
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

# ── Recognised lifecycle states (mirrors LifecycleState) ───────────────

_VALID_STATES: frozenset[str] = frozenset({
    "created", "queued", "routing", "context_retrieval",
    "in_meeting", "consensus_building", "validating", "executing",
    "finalizing", "completed", "paused", "deadlocked",
    "escalated", "cancelled", "failed", "stale",
})

# ── Valid priority levels ──────────────────────────────────────────────

_VALID_PRIORITIES: frozenset[str] = frozenset({"p0", "p1", "p2", "p3"})

# ── Valid round types ──────────────────────────────────────────────────

_VALID_ROUND_TYPES: frozenset[str] = frozenset({
    "opinion",
    "conflict_resolution",
    "convergence",
    "tie_break",
})

# ── Agenda type enum (mirrors qwen_router AGENDA_TYPES) ────────────────

_VALID_AGENDA_TYPES: frozenset[str] = frozenset({
    "creative_production",
    "technical_development",
    "marketing_strategy",
    "risk_assessment",
    "general_planning",
    "project_review",
})

# ── Expected field descriptors ─────────────────────────────────────────

_CORE_FIELDS: tuple[dict[str, Any], ...] = (
    # ── Identity ──
    {
        "name": "meeting_id",
        "kind": "non_empty_string",
        "required": True,
    },
    {
        "name": "role_id",
        "kind": "non_empty_string",
        "required": True,
    },
    # ── Lifecycle ──
    {
        "name": "state",
        "kind": "enum_string",
        "enum": _VALID_STATES,
        "required": True,
    },
    # ── Meeting context ──
    {
        "name": "priority",
        "kind": "enum_string",
        "enum": _VALID_PRIORITIES,
        "required": True,
    },
    {
        "name": "agenda",
        "kind": "non_empty_string",
        "required": True,
    },
    {
        "name": "agenda_type",
        "kind": "enum_string",
        "enum": _VALID_AGENDA_TYPES,
        "required": True,
    },
    {
        "name": "tags",
        "kind": "string_array",
        "required": True,
    },
    {
        "name": "risk_tags",
        "kind": "string_array",
        "required": True,
    },
    # ── Round context ──
    {
        "name": "round",
        "kind": "positive_int_or_tie_break",
        "required": True,
    },
    {
        "name": "round_type",
        "kind": "enum_string",
        "enum": _VALID_ROUND_TYPES,
        "required": True,
    },
    {
        "name": "round_context",
        "kind": "non_empty_string",
        "required": True,
    },
    # ── Token budget ──
    {
        "name": "token_budget",
        "kind": "positive_int",
        "required": True,
    },
    # ── Previous rounds (list, may be empty for round 1) ──
    {
        "name": "previous_rounds",
        "kind": "list",
        "required": True,
    },
)

_OPTIONAL_FIELDS: tuple[dict[str, Any], ...] = (
    {
        "name": "previous_rounds_data",
        "kind": "object_or_none",
        "required": False,
    },
    {
        "name": "knowledge_context",
        "kind": "string_or_none",
        "required": False,
    },
    {
        "name": "evidence_summary",
        "kind": "string_or_none",
        "required": False,
    },
    {
        "name": "rebuttal_points",
        "kind": "string_array_or_none",
        "required": False,
    },
    {
        "name": "unresolved_issues",
        "kind": "string_array_or_none",
        "required": False,
    },
    {
        "name": "participant_roles",
        "kind": "string_array_or_none",
        "required": False,
    },
)

ALL_VALIDATED_FIELDS: tuple[dict[str, Any], ...] = (
    _CORE_FIELDS + _OPTIONAL_FIELDS
)

# ── Packet schema version ──────────────────────────────────────────────

PACKET_SCHEMA_VERSION = "context-packet-validation.v1"


# ── Data types ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PacketFieldError:
    """A single field-level validation failure in a context packet.

    Carries enough context for the Coordinator to log, report, or
    auto-correct without re-parsing the raw payload.
    """

    field_name: str
    """Name of the field that failed validation."""

    error_type: str
    """Category: ``missing``, ``wrong_type``, ``invalid_value``,
    ``empty_string``, ``out_of_range``."""

    message: str
    """Human-readable description of the failure."""

    expected: str
    """What the validator expected (e.g. ``non-empty string``)."""

    actual: str
    """What was actually received (Python type name or value repr)."""


@dataclass(frozen=True)
class PacketValidationReport:
    """Aggregated result of validating a context packet.

    ``passed`` is ``True`` only when **zero** mandatory-field errors
    were detected.  ``errors`` is a tuple of all failures — both
    mandatory and optional — for full visibility.
    """

    passed: bool
    """Overall validation result."""

    errors: tuple[PacketFieldError, ...]
    """All detected validation failures (empty when passed)."""

    total_fields_checked: int
    """Number of fields that were inspected."""

    schema_version: str = PACKET_SCHEMA_VERSION
    """Schema version for this validator."""

    @property
    def error_count(self) -> int:
        """Convenience: total number of errors."""
        return len(self.errors)

    @property
    def mandatory_errors(self) -> tuple[PacketFieldError, ...]:
        """Only errors on required (mandatory) fields."""
        mandatory_names = {d["name"] for d in _CORE_FIELDS}
        return tuple(
            e for e in self.errors if e.field_name in mandatory_names
        )

    def errors_by_field(self) -> dict[str, tuple[PacketFieldError, ...]]:
        """Group errors by field name for targeted reporting."""
        grouped: dict[str, list[PacketFieldError]] = {}
        for err in self.errors:
            grouped.setdefault(err.field_name, []).append(err)
        return {k: tuple(v) for k, v in grouped.items()}

    def missing_fields(self) -> tuple[str, ...]:
        """Return names of required fields that are missing."""
        return tuple(
            e.field_name
            for e in self.errors
            if e.error_type == "missing"
            and e.field_name in {d["name"] for d in _CORE_FIELDS}
        )


# ── Individual field validators ────────────────────────────────────────

def _validate_non_empty_string(
    name: str, value: Any
) -> list[PacketFieldError]:
    """Validate a field is a non-empty string (after stripping)."""
    errors: list[PacketFieldError] = []

    if value is None:
        errors.append(
            PacketFieldError(
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
            PacketFieldError(
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

    stripped = value.strip()
    if not stripped:
        errors.append(
            PacketFieldError(
                field_name=name,
                error_type="empty_string",
                message=f"'{name}' is empty or whitespace-only.",
                expected="non-empty string",
                actual=repr(value)[:80],
            )
        )

    return errors


def _validate_enum_string(
    name: str, value: Any, enum: frozenset[str]
) -> list[PacketFieldError]:
    """Validate a field is a string from a restricted enum set."""
    errors: list[PacketFieldError] = []

    if value is None:
        errors.append(
            PacketFieldError(
                field_name=name,
                error_type="missing",
                message=(
                    f"'{name}' is None; expected one of {sorted(enum)}"
                ),
                expected=f"one of {sorted(enum)}",
                actual="None",
            )
        )
        return errors

    if not isinstance(value, str):
        errors.append(
            PacketFieldError(
                field_name=name,
                error_type="wrong_type",
                message=(
                    f"'{name}' must be a string from {sorted(enum)}, "
                    f"got {type(value).__name__}"
                ),
                expected=f"one of {sorted(enum)}",
                actual=repr(value)[:120],
            )
        )
        return errors

    stripped = value.strip().lower()
    if stripped not in enum:
        errors.append(
            PacketFieldError(
                field_name=name,
                error_type="invalid_value",
                message=(
                    f"'{name}' value '{value}' is not recognised. "
                    f"Valid: {sorted(enum)}"
                ),
                expected=f"one of {sorted(enum)}",
                actual=repr(value),
            )
        )

    return errors


def _validate_string_array(
    name: str, value: Any
) -> list[PacketFieldError]:
    """Validate a field is a list of strings (homogeneous, may be empty)."""
    errors: list[PacketFieldError] = []

    if value is None:
        errors.append(
            PacketFieldError(
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
            PacketFieldError(
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

    return errors


def _validate_positive_int_or_tie_break(
    name: str, value: Any
) -> list[PacketFieldError]:
    """Validate a field is a positive integer (1-4) or 'tie_break'."""
    errors: list[PacketFieldError] = []

    if value is None:
        errors.append(
            PacketFieldError(
                field_name=name,
                error_type="missing",
                message=f"'{name}' is None; expected 1-4 or 'tie_break'.",
                expected="int 1-4 or 'tie_break'",
                actual="None",
            )
        )
        return errors

    if isinstance(value, str):
        if value.strip().lower() != "tie_break":
            errors.append(
                PacketFieldError(
                    field_name=name,
                    error_type="invalid_value",
                    message=(
                        f"'{name}' string value '{value}' is not "
                        f"'tie_break'. Use integer 1-4 or 'tie_break'."
                    ),
                    expected="int 1-4 or 'tie_break'",
                    actual=repr(value),
                )
            )
        return errors

    if isinstance(value, bool):
        errors.append(
            PacketFieldError(
                field_name=name,
                error_type="wrong_type",
                message=(
                    f"'{name}' is a boolean ({value}); expected a "
                    f"positive integer or 'tie_break'."
                ),
                expected="int 1-4 or 'tie_break'",
                actual=f"bool = {value}",
            )
        )
        return errors

    if isinstance(value, (int, float)):
        if isinstance(value, float) and value != int(value):
            errors.append(
                PacketFieldError(
                    field_name=name,
                    error_type="wrong_type",
                    message=(
                        f"'{name}' is a float ({value}); expected "
                        f"an integer."
                    ),
                    expected="int 1-4 or 'tie_break'",
                    actual=f"float = {value}",
                )
            )
            return errors
        ivalue = int(value)
        if ivalue < 1 or ivalue > 4:
            errors.append(
                PacketFieldError(
                    field_name=name,
                    error_type="out_of_range",
                    message=(
                        f"'{name}' = {ivalue} is outside [1, 4]. "
                        f"Use 1-4 or 'tie_break'."
                    ),
                    expected="int 1-4",
                    actual=str(ivalue),
                )
            )
        return errors

    errors.append(
        PacketFieldError(
            field_name=name,
            error_type="wrong_type",
            message=(
                f"'{name}' must be an int or 'tie_break', "
                f"got {type(value).__name__}"
            ),
            expected="int 1-4 or 'tie_break'",
            actual=type(value).__name__,
        )
    )
    return errors


def _validate_positive_int(
    name: str, value: Any
) -> list[PacketFieldError]:
    """Validate a field is a positive integer (>0)."""
    errors: list[PacketFieldError] = []

    if value is None:
        errors.append(
            PacketFieldError(
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
            PacketFieldError(
                field_name=name,
                error_type="wrong_type",
                message=(
                    f"'{name}' is a boolean ({value}); expected "
                    f"a positive integer."
                ),
                expected="int > 0",
                actual=f"bool = {value}",
            )
        )
        return errors

    if isinstance(value, (int, float)):
        if isinstance(value, float) and value != int(value):
            errors.append(
                PacketFieldError(
                    field_name=name,
                    error_type="wrong_type",
                    message=(
                        f"'{name}' is a float ({value}); expected "
                        f"an integer."
                    ),
                    expected="int > 0",
                    actual=f"float = {value}",
                )
            )
            return errors
        ivalue = int(value)
        if ivalue <= 0:
            errors.append(
                PacketFieldError(
                    field_name=name,
                    error_type="out_of_range",
                    message=(
                        f"'{name}' = {ivalue} is not positive. "
                        f"Must be > 0."
                    ),
                    expected="int > 0",
                    actual=str(ivalue),
                )
            )
        return errors

    errors.append(
        PacketFieldError(
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


def _validate_list(
    name: str, value: Any
) -> list[PacketFieldError]:
    """Validate a field is a list (any content)."""
    errors: list[PacketFieldError] = []

    if value is None:
        errors.append(
            PacketFieldError(
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
            PacketFieldError(
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


def _validate_object_or_none(
    name: str, value: Any
) -> list[PacketFieldError]:
    """Validate a field is a dict or None (optional field)."""
    errors: list[PacketFieldError] = []

    if value is None:
        return errors  # None is acceptable

    if not isinstance(value, dict):
        errors.append(
            PacketFieldError(
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
) -> list[PacketFieldError]:
    """Validate a field is a string or None (optional field)."""
    errors: list[PacketFieldError] = []

    if value is None:
        return errors  # None is acceptable

    if not isinstance(value, str):
        errors.append(
            PacketFieldError(
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


def _validate_string_array_or_none(
    name: str, value: Any
) -> list[PacketFieldError]:
    """Validate a field is a list of strings or None (optional)."""
    errors: list[PacketFieldError] = []

    if value is None:
        return errors  # None is acceptable

    if not isinstance(value, list):
        errors.append(
            PacketFieldError(
                field_name=name,
                error_type="wrong_type",
                message=(
                    f"'{name}' must be a list or None, "
                    f"got {type(value).__name__}"
                ),
                expected="list[str] or None",
                actual=type(value).__name__,
            )
        )

    return errors


# ── Validator dispatch ─────────────────────────────────────────────────

_VALIDATORS: dict[str, Callable[[str, Any], list[PacketFieldError]]] = {
    "non_empty_string": _validate_non_empty_string,
    "enum_string": lambda n, v, **kw: _validate_enum_string(
        n, v, kw.get("enum", frozenset())
    ),
    "string_array": _validate_string_array,
    "positive_int_or_tie_break": _validate_positive_int_or_tie_break,
    "positive_int": _validate_positive_int,
    "list": _validate_list,
    "object_or_none": _validate_object_or_none,
    "string_or_none": _validate_string_or_none,
    "string_array_or_none": _validate_string_array_or_none,
}


def _get_validator(descriptor: dict[str, Any]) -> (
    Callable[[str, Any], list[PacketFieldError]] | None
):
    """Resolve a validator callable from a field descriptor."""
    kind = descriptor["kind"]
    if kind == "enum_string":
        enum = descriptor.get("enum", frozenset())
        return lambda n, v: _validate_enum_string(n, v, enum)
    return _VALIDATORS.get(kind)


# ── Public API ─────────────────────────────────────────────────────────

def validate_context_packet(
    data: dict[str, Any] | None,
) -> PacketValidationReport:
    """Validate a context packet against the required field schema.

    This is the main entry point for **Sub-AC 6.1.1**.  It accepts a
    dict parsed from the Coordinator's context packet JSON and validates
    every field against the expected type and value constraints.

    **Behaviour:**

    * All 14 core fields are checked — no early-exit on first error.
    * Optional fields are validated when present but their absence
      does **not** cause a failure.
    * ``None`` input is treated as a single fatal error.
    * The ``passed`` flag is ``True`` only when zero mandatory-field
      errors are detected.  Optional-field errors do not cause
      ``passed`` to become ``False`` (they are warnings).

    Args:
        data: The parsed context packet dict (or ``None``).

    Returns:
        ``PacketValidationReport`` with ``passed=True`` when all
        mandatory fields are present and valid.

    Examples:
        Valid complete packet:

        >>> report = validate_context_packet({
        ...     "meeting_id": "meeting_20260101_abc123",
        ...     "role_id": "art-director",
        ...     "state": "in_meeting",
        ...     "priority": "p2",
        ...     "agenda": "Music video opening ideas",
        ...     "agenda_type": "creative_production",
        ...     "tags": ["mv", "visual"],
        ...     "risk_tags": [],
        ...     "round": 1,
        ...     "round_type": "opinion",
        ...     "round_context": "Propose ideas for the MV opening...",
        ...     "token_budget": 12000,
        ...     "previous_rounds": [],
        ... })
        >>> report.passed
        True

        Empty packet:

        >>> report = validate_context_packet({})
        >>> report.passed
        False
        >>> report.error_count
        13

        Missing one required field:

        >>> report = validate_context_packet({
        ...     "meeting_id": "m1",
        ...     "state": "in_meeting",
        ...     "priority": "p2",
        ...     "agenda": "Test",
        ...     "agenda_type": "general_planning",
        ...     "tags": [],
        ...     "risk_tags": [],
        ...     "round": 1,
        ...     "round_type": "opinion",
        ...     "round_context": "...",
        ...     "token_budget": 12000,
        ...     "previous_rounds": [],
        ... })
        >>> report.passed
        False
        >>> "role_id" in report.missing_fields()
        True
    """
    all_errors: list[PacketFieldError] = []

    # ── Null guard ──
    if data is None:
        return PacketValidationReport(
            passed=False,
            errors=(
                PacketFieldError(
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
        return PacketValidationReport(
            passed=False,
            errors=(
                PacketFieldError(
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
    for descriptor in ALL_VALIDATED_FIELDS:
        name: str = descriptor["name"]
        required: bool = descriptor.get("required", False)

        if name not in data:
            if required:
                all_errors.append(
                    PacketFieldError(
                        field_name=name,
                        error_type="missing",
                        message=(
                            f"Required field '{name}' is missing from "
                            f"context packet."
                        ),
                        expected=descriptor["kind"],
                        actual="(absent)",
                    )
                )
            continue

        value = data[name]
        validator = _get_validator(descriptor)
        if validator is None:
            continue

        all_errors.extend(validator(name, value))

    # ── Pass/fail: only mandatory-field errors cause failure ──
    mandatory_names = {d["name"] for d in _CORE_FIELDS}
    mandatory_errors = [
        e for e in all_errors if e.field_name in mandatory_names
    ]

    return PacketValidationReport(
        passed=len(mandatory_errors) == 0,
        errors=tuple(all_errors),
        total_fields_checked=len(ALL_VALIDATED_FIELDS),
    )


# ── Convenience: validate with exception ──────────────────────────────

class PacketValidationError(ValueError):
    """Raised when a context packet fails mandatory-field validation.

    Carries the full ``PacketValidationReport`` for inspection.
    """

    def __init__(self, report: PacketValidationReport) -> None:
        missing = report.missing_fields()
        msg = (
            f"Context packet validation failed: "
            f"{report.error_count} error(s)."
        )
        if missing:
            msg += f" Missing fields: {', '.join(missing)}"
        super().__init__(msg)
        self.report = report


def validate_or_raise(
    data: dict[str, Any] | None,
) -> PacketValidationReport:
    """Validate a context packet and raise on failure.

    Convenience wrapper around ``validate_context_packet()`` that
    raises ``PacketValidationError`` when mandatory-field validation
    fails.  Useful for code paths that cannot proceed without a
    valid packet.

    Args:
        data: The parsed context packet dict (or ``None``).

    Returns:
        ``PacketValidationReport`` (guaranteed ``passed=True``).

    Raises:
        PacketValidationError: If mandatory fields are missing or
                               invalid.
    """
    report = validate_context_packet(data)
    if not report.passed:
        raise PacketValidationError(report)
    return report


# ── Exports ────────────────────────────────────────────────────────────

__all__ = [
    "PacketFieldError",
    "PacketValidationError",
    "PacketValidationReport",
    "ALL_VALIDATED_FIELDS",
    "PACKET_SCHEMA_VERSION",
    "validate_context_packet",
    "validate_or_raise",
]
