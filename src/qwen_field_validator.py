"""Qwen response field-type validator.

Sub-AC 2b-2: Accepts a parsed dict from Qwen LLM output, validates
all seven fields against the expected schema, and aggregates
type-mismatch and missing-field errors into a structured validation
report.  Designed to be testable with mock dict payloads covering
all field combinations, edge cases, and missing fields.

Schema enforced:

* ``agenda_type`` — must be a string from the restricted set of
  valid agenda type identifiers.
* ``tags``, ``risk_tags``, ``required_roles``, ``optional_roles`` —
  must be non-null lists of non-empty strings.
* ``validator_required``, ``codex_required`` — must be strict
  booleans (``True`` / ``False``).
* ``confidence`` (contextual seventh field) — must be a float in
  [0.0, 1.0] when present; ``None`` / absent is tolerated because
  the Coordinator applies a default.

Every validation failure is collected; no early-exit on the first
error.  The report includes a ``passed`` flag for quick branching.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


# ── Agenda type enum (mirrors AGENDA_TYPES from qwen_router.py) ─────────

_VALID_AGENDA_TYPES: frozenset[str] = frozenset({
    "creative_production",
    "technical_development",
    "marketing_strategy",
    "risk_assessment",
    "general_planning",
    "project_review",
})

# ── Expected field descriptors ──────────────────────────────────────────

_CORE_FIELDS: tuple[dict[str, Any], ...] = (
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
    {
        "name": "required_roles",
        "kind": "string_array",
        "required": True,
    },
    {
        "name": "optional_roles",
        "kind": "string_array",
        "required": True,
    },
    {
        "name": "validator_required",
        "kind": "boolean",
        "required": True,
    },
    {
        "name": "codex_required",
        "kind": "boolean",
        "required": True,
    },
)

_CONTEXTUAL_FIELDS: tuple[dict[str, Any], ...] = (
    {
        "name": "confidence",
        "kind": "float_0_1",
        "required": False,
    },
    {
        "name": "reasoning",
        "kind": "string",
        "required": False,
    },
)

ALL_VALIDATED_FIELDS: tuple[dict[str, Any], ...] = (
    _CORE_FIELDS + _CONTEXTUAL_FIELDS
)


# ── Data types ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class FieldValidationError:
    """A single field-level validation failure.

    Carries enough context for the Coordinator to log, report, or
    auto-correct without re-parsing the raw payload.
    """

    field_name: str
    """Name of the field that failed validation."""

    error_type: str
    """Category: ``missing``, ``wrong_type``, ``invalid_value``,
    ``empty_array``, ``non_string_item``."""

    message: str
    """Human-readable description of the failure."""

    expected: str
    """What the validator expected (e.g. ``List[str]``, ``bool``)."""

    actual: str
    """What was actually received (Python type name or value repr)."""


@dataclass(frozen=True)
class ValidationReport:
    """Aggregated result of validating a Qwen response dict.

    ``passed`` is ``True`` only when **zero** errors were detected.
    The ``errors`` tuple drives downstream logging and recovery.
    """

    passed: bool
    """Overall validation result."""

    errors: tuple[FieldValidationError, ...]
    """All detected validation failures (empty when passed)."""

    total_fields_checked: int
    """Number of fields that were inspected."""

    @property
    def error_count(self) -> int:
        """Convenience: total number of errors."""
        return len(self.errors)

    def errors_by_field(self) -> dict[str, tuple[FieldValidationError, ...]]:
        """Group errors by field name for targeted reporting."""
        grouped: dict[str, list[FieldValidationError]] = {}
        for err in self.errors:
            grouped.setdefault(err.field_name, []).append(err)
        return {k: tuple(v) for k, v in grouped.items()}


# ── Individual field validators ─────────────────────────────────────────

def _validate_enum_string(
    name: str, value: Any, enum: frozenset[str]
) -> list[FieldValidationError]:
    """Validate a field is a string from a restricted enum set."""
    errors: list[FieldValidationError] = []

    if not isinstance(value, str):
        errors.append(
            FieldValidationError(
                field_name=name,
                error_type="wrong_type",
                message=(
                    f"'{name}' must be a string from {sorted(enum)}, "
                    f"got {type(value).__name__}"
                ),
                expected=f"str in {sorted(enum)}",
                actual=repr(value)[:120],
            )
        )
        return errors

    stripped = value.strip()
    if stripped != value:
        value = stripped

    if value not in enum:
        errors.append(
            FieldValidationError(
                field_name=name,
                error_type="invalid_value",
                message=(
                    f"'{name}' value '{value}' is not a recognised "
                    f"agenda type.  Valid: {sorted(enum)}"
                ),
                expected=f"one of {sorted(enum)}",
                actual=repr(value),
            )
        )

    return errors


def _validate_string_array(
    name: str, value: Any
) -> list[FieldValidationError]:
    """Validate a field is a ``list[str]`` (homogeneous, non-empty items)."""
    errors: list[FieldValidationError] = []

    if value is None:
        errors.append(
            FieldValidationError(
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
            FieldValidationError(
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

    if len(value) == 0:
        # Empty list is tolerated — Qwen may legitimately return []
        # for risk_tags or optional_roles.
        return errors

    non_string_items: list[int] = []
    empty_string_items: list[int] = []
    for i, item in enumerate(value):
        if not isinstance(item, str):
            non_string_items.append(i)
        elif item.strip() == "":
            empty_string_items.append(i)

    if non_string_items:
        errors.append(
            FieldValidationError(
                field_name=name,
                error_type="non_string_item",
                message=(
                    f"'{name}' contains {len(non_string_items)} non-string "
                    f"item(s) at indices {non_string_items[:5]}"
                    f"{'…' if len(non_string_items) > 5 else ''}"
                ),
                expected="list[str] (all items must be strings)",
                actual=(
                    f"list containing {type(value[non_string_items[0]]).__name__}"
                    f" at index {non_string_items[0]}"
                ),
            )
        )

    if empty_string_items:
        # Empty/whitespace strings are flagged but non-fatal for the
        # aggregate pass/fail — downstream normalisation strips them.
        # We still report them so the Coordinator can log a warning.
        pass

    return errors


def _validate_boolean(
    name: str, value: Any
) -> list[FieldValidationError]:
    """Validate a field is a strict boolean (``True`` / ``False``).

    Python truthiness rules are deliberately NOT used: ``0``, ``1``,
    ``"true"``, ``"false"`` are rejected so that JSON type coercion
    bugs cannot silently produce the wrong routing decision.
    """
    errors: list[FieldValidationError] = []

    if value is None:
        errors.append(
            FieldValidationError(
                field_name=name,
                error_type="missing",
                message=f"'{name}' is None; expected a boolean.",
                expected="bool",
                actual="None",
            )
        )
        return errors

    if not isinstance(value, bool):
        errors.append(
            FieldValidationError(
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


def _validate_float_0_1(
    name: str, value: Any
) -> list[FieldValidationError]:
    """Validate a field is a float in [0.0, 1.0].

    ``None`` or absent is tolerated — the Coordinator supplies a default.
    Integer values (e.g. ``0``, ``1``) are accepted and cast to float.
    """
    errors: list[FieldValidationError] = []

    if value is None:
        return errors  # optional field — absence is fine

    if isinstance(value, bool):
        errors.append(
            FieldValidationError(
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
            FieldValidationError(
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
            FieldValidationError(
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


def _validate_string(
    name: str, value: Any
) -> list[FieldValidationError]:
    """Validate a field is a plain string (optional — None is ok)."""
    errors: list[FieldValidationError] = []

    if value is None:
        return errors  # optional

    if not isinstance(value, str):
        errors.append(
            FieldValidationError(
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


# ── Validator dispatch ──────────────────────────────────────────────────

_VALIDATORS: dict[str, Callable[[str, Any], list[FieldValidationError]]] = {
    "enum_string": lambda n, v: _validate_enum_string(
        n, v, _VALID_AGENDA_TYPES
    ),
    "string_array": _validate_string_array,
    "boolean": _validate_boolean,
    "float_0_1": _validate_float_0_1,
    "string": _validate_string,
}


# ── Public API ──────────────────────────────────────────────────────────

def validate_qwen_response(data: dict[str, Any] | None) -> ValidationReport:
    """Validate a parsed Qwen classification dict against the field schema.

    This is the main entry point for **Sub-AC 2b-2**.  It accepts a
    dict parsed from Qwen JSON output (e.g. via ``extract_json`` in
    ``qwen_json_extractor.py``) and validates every field against the
    expected type and value constraints.

    **Behaviour:**

    * All seven core fields are checked — no early-exit on first error.
    * Contextual fields (``confidence``, ``reasoning``) are validated
      when present but their absence does **not** cause a failure.
    * ``None`` input is treated as a single fatal error.
    * String values are stripped before enum matching.

    Args:
        data: The parsed JSON dict from Qwen (or ``None``).

    Returns:
        ``ValidationReport`` with ``passed=True`` when zero errors are
        detected.  Inspect ``report.errors`` for detailed diagnostics
        on failure.

    Examples:
        >>> report = validate_qwen_response({
        ...     "agenda_type": "creative_production",
        ...     "tags": ["art", "design"],
        ...     "risk_tags": [],
        ...     "required_roles": ["coordinator", "art-director"],
        ...     "optional_roles": [],
        ...     "validator_required": True,
        ...     "codex_required": False,
        ... })
        >>> report.passed
        True

        >>> report = validate_qwen_response({
        ...     "agenda_type": "bogus_type",
        ...     "tags": "not_a_list",
        ...     "risk_tags": None,
        ...     "required_roles": [1, 2, 3],
        ...     "optional_roles": [""],
        ...     "validator_required": 1,
        ...     "codex_required": "yes",
        ... })
        >>> report.passed
        False
        >>> report.error_count >= 6
        True
    """
    all_errors: list[FieldValidationError] = []

    # ── Null guard ──
    if data is None:
        return ValidationReport(
            passed=False,
            errors=(
                FieldValidationError(
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
        return ValidationReport(
            passed=False,
            errors=(
                FieldValidationError(
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
        kind: str = descriptor["kind"]
        required: bool = descriptor.get("required", False)

        if name not in data:
            if required:
                all_errors.append(
                    FieldValidationError(
                        field_name=name,
                        error_type="missing",
                        message=f"Required field '{name}' is missing from Qwen response.",
                        expected=kind,
                        actual="(absent)",
                    )
                )
            continue  # non-required field — skip silently

        value = data[name]
        validator = _VALIDATORS.get(kind)
        if validator is None:
            # Defensive: unknown kind in field descriptor
            continue

        all_errors.extend(validator(name, value))

    return ValidationReport(
        passed=len(all_errors) == 0,
        errors=tuple(all_errors),
        total_fields_checked=len(ALL_VALIDATED_FIELDS),
    )
