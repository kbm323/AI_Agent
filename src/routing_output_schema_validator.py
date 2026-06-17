"""Schema validator for routing output data — Sub-AC 3.1c.

Validates routing results (from Qwen LLM classification or static fallback
matching) against the defined ``output_schema.fields`` schema from
``routing_rules.yaml``.

This is a **runtime validation** step — every routing result passes through
this validator before the Coordinator consumes it.  The validator:

* **Accepts** data matching required fields, types, and structure constraints.
* **Rejects** schema violations with **actionable field-level errors**
  identifying the violating key and the expected format.
* **Aggregates all errors** — no early-exit on the first failure.

Design principles
-----------------
* **Follows existing patterns** from :mod:`routing_rules_validator` and
  :mod:`qwen_field_validator` for consistency.
* **Independent of YAML loading** — schema is defined declaratively as a
  ``RoutingOutputSchema`` data structure, making it testable without
  filesystem dependencies.
* **Exact type enforcement** — e.g. ``bool`` is ``True``/``False`` only,
  not truthy/falsy values.
* **Descriptive errors** — every violation carries the field name, expected
  type/constraint, and actual value observed.

Schema enforced (mirrors ``output_schema.fields``)
---------------------------------------------------
* ``agenda_type``          — non-empty string
* ``agenda_label``         — string (tolerated when empty)
* ``tags``                 — list of strings
* ``risk_tags``            — list of strings (empty allowed)
* ``required_roles``       — list of non-empty strings
* ``optional_roles``       — list of strings (empty allowed)
* ``validator_required``   — strict boolean (True/False)
* ``codex_required``       — strict boolean (True/False)
* ``priority``             — one of P0, P1, P2, P3
* ``routing_source``       — non-empty string
* ``routing_reason``       — non-empty string
* ``confidence``           — float in [0.0, 1.0]
* ``generated_at``         — ISO 8601 timestamp string
* ``version``              — semver string (X.Y.Z)
"""

from __future__ import annotations

import re as _re
from dataclasses import dataclass, field
from typing import Any


# ═══════════════════════════════════════════════════════════════════════════
# Custom exception
# ═══════════════════════════════════════════════════════════════════════════


class RoutingOutputValidationError(ValueError):
    """Raised when routing output data fails schema validation.

    Carries the full :class:`RoutingOutputValidationReport` so callers can
    inspect individual failures programmatically without string-parsing the
    exception message.
    """

    def __init__(self, report: RoutingOutputValidationReport) -> None:
        self.report = report
        super().__init__(_format_report_summary(report))


# ═══════════════════════════════════════════════════════════════════════════
# Data types
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class RoutingOutputViolation:
    """A single schema-level validation failure in a routing output.

    Attributes:
        field: Name of the field that failed validation.
        error_type: Category — ``missing_field``, ``wrong_type``,
            ``invalid_value``, ``empty_field``, ``unknown_field``.
        message: Human-readable description.
        expected: What was expected (type name or value desc).
        actual: What was observed (type name or value repr).
    """

    field: str
    error_type: str
    message: str
    expected: str = ""
    actual: str = ""


@dataclass(frozen=True)
class RoutingOutputValidationReport:
    """Aggregated result of validating a routing output dict.

    ``passed`` is ``True`` only when zero violations were detected.
    """

    passed: bool
    violations: tuple[RoutingOutputViolation, ...] = ()
    fields_checked: int = 0

    @property
    def error_count(self) -> int:
        """Total number of violations detected."""
        return len(self.violations)

    def violations_by_field(self) -> dict[str, tuple[RoutingOutputViolation, ...]]:
        """Group violations by field name for targeted reporting."""
        grouped: dict[str, list[RoutingOutputViolation]] = {}
        for v in self.violations:
            grouped.setdefault(v.field, []).append(v)
        return {k: tuple(v) for k, v in grouped.items()}


# ═══════════════════════════════════════════════════════════════════════════
# Schema definition
# ═══════════════════════════════════════════════════════════════════════════

# Recognised priority values
_VALID_PRIORITIES: frozenset[str] = frozenset({"P0", "P1", "P2", "P3"})

# SemVer pattern
_SEMVER_RE = _re.compile(r"^\d+\.\d+\.\d+$")

# ISO 8601 timestamp pattern (basic — full spec is complex)
_ISO8601_RE = _re.compile(
    r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}"
)


@dataclass(frozen=True)
class RoutingOutputSchema:
    """Declarative schema definition for a routing output field.

    Attributes:
        field_name: The key in the routing output dict.
        kind: Type category — one of ``string``, ``string_list``,
            ``boolean``, ``number``, ``priority``, ``semver``,
            ``iso8601``.
        required: Whether the field must be present and non-null.
        allow_empty: For strings/lists, whether empty values are OK.
        min_val: For numbers, minimum allowed value.
        max_val: For numbers, maximum allowed value.
    """

    field_name: str
    kind: str
    required: bool = True
    allow_empty: bool = False
    min_val: float | None = None
    max_val: float | None = None


# The canonical routing output schema — mirrors output_schema.fields
# from routing_rules.yaml exactly.
ROUTING_OUTPUT_SCHEMA: tuple[RoutingOutputSchema, ...] = (
    RoutingOutputSchema("agenda_type", "string", required=True, allow_empty=False),
    RoutingOutputSchema("agenda_label", "string", required=True, allow_empty=True),
    RoutingOutputSchema("tags", "string_list", required=True, allow_empty=True),
    RoutingOutputSchema("risk_tags", "string_list", required=True, allow_empty=True),
    RoutingOutputSchema("required_roles", "string_list", required=True, allow_empty=True),
    RoutingOutputSchema("optional_roles", "string_list", required=True, allow_empty=True),
    RoutingOutputSchema("validator_required", "boolean", required=True),
    RoutingOutputSchema("codex_required", "boolean", required=True),
    RoutingOutputSchema("priority", "priority", required=True),
    RoutingOutputSchema("routing_source", "string", required=True, allow_empty=False),
    RoutingOutputSchema("routing_reason", "string", required=True, allow_empty=False),
    RoutingOutputSchema("confidence", "number", required=True, min_val=0.0, max_val=1.0),
    RoutingOutputSchema("generated_at", "iso8601", required=True),
    RoutingOutputSchema("version", "semver", required=True),
)

# Build a lookup map: field_name → schema
_SCHEMA_MAP: dict[str, RoutingOutputSchema] = {
    s.field_name: s for s in ROUTING_OUTPUT_SCHEMA
}

# Known field names for unknown-key detection
_KNOWN_FIELDS: frozenset[str] = frozenset(_SCHEMA_MAP.keys())

# Additional fields that downstream enrichers may add — tolerated, not validated
_TOLERATED_EXTRA_FIELDS: frozenset[str] = frozenset({
    "teams",
    "requires_approval",
    "exit_condition",
    "reasoning",
})


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════


def _violation(field: str, error_type: str, message: str,
               expected: str = "", actual: str = "") -> RoutingOutputViolation:
    """Convenience constructor for RoutingOutputViolation."""
    return RoutingOutputViolation(
        field=field, error_type=error_type, message=message,
        expected=expected, actual=actual,
    )


def _format_report_summary(report: RoutingOutputValidationReport) -> str:
    """Build a one-line summary for the exception message."""
    if report.passed:
        return "Routing output validation passed"
    top_fields = list(report.violations_by_field().keys())[:5]
    return (
        f"Routing output validation failed with {report.error_count} "
        f"violation(s) in fields: {', '.join(top_fields)}"
    )


def _safe_repr(value: object, max_len: int = 80) -> str:
    """Safely repr a value, truncating to *max_len*."""
    r = repr(value)
    if len(r) > max_len:
        r = r[:max_len - 3] + "..."
    return r


# ═══════════════════════════════════════════════════════════════════════════
# Per-kind validators
# ═══════════════════════════════════════════════════════════════════════════


def _validate_string_field(
    value: Any, schema: RoutingOutputSchema,
) -> list[RoutingOutputViolation]:
    """Validate a string-typed field."""
    violations: list[RoutingOutputViolation] = []

    if not isinstance(value, str):
        violations.append(_violation(
            schema.field_name, "wrong_type",
            f"'{schema.field_name}' must be a string, got {type(value).__name__}",
            expected="str", actual=_safe_repr(value),
        ))
        return violations

    if not schema.allow_empty and value.strip() == "":
        violations.append(_violation(
            schema.field_name, "empty_field",
            f"'{schema.field_name}' must not be empty",
            expected="non-empty str", actual=_safe_repr(value),
        ))

    return violations


def _validate_string_list_field(
    value: Any, schema: RoutingOutputSchema,
) -> list[RoutingOutputViolation]:
    """Validate a list-of-strings field."""
    violations: list[RoutingOutputViolation] = []

    if value is None:
        violations.append(_violation(
            schema.field_name, "missing_field",
            f"'{schema.field_name}' is None; expected a list of strings",
            expected="list[str]", actual="None",
        ))
        return violations

    if not isinstance(value, (list, tuple)):
        violations.append(_violation(
            schema.field_name, "wrong_type",
            f"'{schema.field_name}' must be a list, got {type(value).__name__}",
            expected="list[str]", actual=_safe_repr(value),
        ))
        return violations

    # Check items are strings
    non_string_idxs: list[int] = []
    for i, item in enumerate(value):
        if not isinstance(item, str):
            non_string_idxs.append(i)

    if non_string_idxs:
        violations.append(_violation(
            schema.field_name, "wrong_type",
            f"'{schema.field_name}' contains {len(non_string_idxs)} non-string "
            f"item(s) at indices {non_string_idxs[:5]}",
            expected="list[str] (all items must be strings)",
            actual=f"list containing {type(value[non_string_idxs[0]]).__name__} "
                   f"at index {non_string_idxs[0]}",
        ))

    return violations


def _validate_boolean_field(
    value: Any, schema: RoutingOutputSchema,
) -> list[RoutingOutputViolation]:
    """Validate a strict boolean field."""
    violations: list[RoutingOutputViolation] = []

    if not isinstance(value, bool):
        violations.append(_violation(
            schema.field_name, "wrong_type",
            f"'{schema.field_name}' must be a boolean (True/False), "
            f"got {type(value).__name__}",
            expected="bool (True or False only)",
            actual=_safe_repr(value),
        ))

    return violations


def _validate_number_field(
    value: Any, schema: RoutingOutputSchema,
) -> list[RoutingOutputViolation]:
    """Validate a numeric field (float or int)."""
    violations: list[RoutingOutputViolation] = []
    min_v = schema.min_val
    max_v = schema.max_val

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        violations.append(_violation(
            schema.field_name, "wrong_type",
            f"'{schema.field_name}' must be a number, got {type(value).__name__}",
            expected="number (int or float)", actual=_safe_repr(value),
        ))
        return violations

    fval = float(value)

    if min_v is not None and fval < min_v:
        violations.append(_violation(
            schema.field_name, "invalid_value",
            f"'{schema.field_name}' = {fval} is below minimum {min_v}",
            expected=f"number >= {min_v}", actual=str(fval),
        ))

    if max_v is not None and fval > max_v:
        violations.append(_violation(
            schema.field_name, "invalid_value",
            f"'{schema.field_name}' = {fval} is above maximum {max_v}",
            expected=f"number <= {max_v}", actual=str(fval),
        ))

    return violations


def _validate_priority_field(
    value: Any, schema: RoutingOutputSchema,
) -> list[RoutingOutputViolation]:
    """Validate a priority field (P0/P1/P2/P3)."""
    violations: list[RoutingOutputViolation] = []

    if not isinstance(value, str):
        violations.append(_violation(
            schema.field_name, "wrong_type",
            f"'{schema.field_name}' must be a priority string, "
            f"got {type(value).__name__}",
            expected=f"one of {sorted(_VALID_PRIORITIES)}",
            actual=_safe_repr(value),
        ))
        return violations

    if value not in _VALID_PRIORITIES:
        violations.append(_violation(
            schema.field_name, "invalid_value",
            f"'{schema.field_name}' = {repr(value)} is not a valid priority. "
            f"Valid: {sorted(_VALID_PRIORITIES)}",
            expected=f"one of {sorted(_VALID_PRIORITIES)}",
            actual=repr(value),
        ))

    return violations


def _validate_semver_field(
    value: Any, schema: RoutingOutputSchema,
) -> list[RoutingOutputViolation]:
    """Validate a SemVer string field."""
    violations: list[RoutingOutputViolation] = []

    if not isinstance(value, str):
        violations.append(_violation(
            schema.field_name, "wrong_type",
            f"'{schema.field_name}' must be a SemVer string, "
            f"got {type(value).__name__}",
            expected="str (e.g. '1.0.0')", actual=_safe_repr(value),
        ))
        return violations

    if not _SEMVER_RE.match(value):
        violations.append(_violation(
            schema.field_name, "invalid_value",
            f"'{schema.field_name}' = {repr(value)} is not a valid SemVer (X.Y.Z)",
            expected="SemVer e.g. '1.0.0'", actual=repr(value),
        ))

    return violations


def _validate_iso8601_field(
    value: Any, schema: RoutingOutputSchema,
) -> list[RoutingOutputViolation]:
    """Validate an ISO 8601 timestamp string field."""
    violations: list[RoutingOutputViolation] = []

    if not isinstance(value, str):
        violations.append(_violation(
            schema.field_name, "wrong_type",
            f"'{schema.field_name}' must be an ISO 8601 timestamp string, "
            f"got {type(value).__name__}",
            expected="str (ISO 8601)", actual=_safe_repr(value),
        ))
        return violations

    if not _ISO8601_RE.match(value):
        violations.append(_violation(
            schema.field_name, "invalid_value",
            f"'{schema.field_name}' = {repr(value)} is not a valid ISO 8601 "
            f"timestamp (expected format: YYYY-MM-DDTHH:MM:SS)",
            expected="ISO 8601 timestamp (e.g. '2026-06-10T12:00:00')",
            actual=repr(value),
        ))

    return violations


# Dispatch table
_VALIDATOR_DISPATCH = {
    "string": _validate_string_field,
    "string_list": _validate_string_list_field,
    "boolean": _validate_boolean_field,
    "number": _validate_number_field,
    "priority": _validate_priority_field,
    "semver": _validate_semver_field,
    "iso8601": _validate_iso8601_field,
}


# ═══════════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════════


def validate_routing_output(
    data: dict[str, Any] | None,
    *,
    schema: tuple[RoutingOutputSchema, ...] | None = None,
    strict_keys: bool = False,
) -> dict[str, Any]:
    """Validate a routing result dict against the output schema.

    This is the **main entry point** for Sub-AC 3.1c.  Accepts a routing
    result dict (from Qwen classification or static fallback matching) and
    validates it against the declared output schema.

    Args:
        data: The routing result dict (e.g. from ``MatchResult.to_dict()``
              or a Qwen ``ClassificationResult`` serialized to dict).
        schema: Optional custom schema definition.  When ``None`` (default),
                uses the canonical ``ROUTING_OUTPUT_SCHEMA``.
        strict_keys: When ``True``, unknown keys outside the schema and
                     tolerated-extra-fields are flagged as violations.
                     Default ``False`` (tolerates extra fields).

    Returns:
        The validated data dict (unchanged) when all checks pass.

    Raises:
        RoutingOutputValidationError: When one or more schema violations
            are detected.  The exception carries a
            :class:`RoutingOutputValidationReport` with the full list of
            :class:`RoutingOutputViolation` objects.

    Examples:
        >>> valid = {
        ...     "agenda_type": "creative-production",
        ...     "agenda_label": "Creative Production",
        ...     "tags": ["art", "design"],
        ...     "risk_tags": [],
        ...     "required_roles": ["art-director"],
        ...     "optional_roles": [],
        ...     "validator_required": True,
        ...     "codex_required": False,
        ...     "priority": "P2",
        ...     "routing_source": "static_fallback",
        ...     "routing_reason": "qwen_timeout",
        ...     "confidence": 0.7,
        ...     "generated_at": "2026-06-10T12:00:00",
        ...     "version": "1.0.0",
        ... }
        >>> validate_routing_output(valid)
        {'agenda_type': 'creative-production', ...}

        >>> invalid = {"agenda_type": 42}
        >>> validate_routing_output(invalid)
        Traceback (most recent call last):
        ...
        RoutingOutputValidationError: Routing output validation failed ...
    """
    s = schema if schema is not None else ROUTING_OUTPUT_SCHEMA
    violations: list[RoutingOutputViolation] = []

    # ── Null / non-dict guard ──
    if data is None:
        violations.append(_violation(
            "<root>", "wrong_type",
            "Input data is None — cannot validate",
            expected="dict[str, Any]", actual="None",
        ))
        report = RoutingOutputValidationReport(
            passed=False, violations=tuple(violations), fields_checked=0,
        )
        raise RoutingOutputValidationError(report)

    if not isinstance(data, dict):
        violations.append(_violation(
            "<root>", "wrong_type",
            f"Input must be a dict, got {type(data).__name__}",
            expected="dict[str, Any]", actual=type(data).__name__,
        ))
        report = RoutingOutputValidationReport(
            passed=False, violations=tuple(violations), fields_checked=0,
        )
        raise RoutingOutputValidationError(report)

    # ── Validate every declared field ──
    for field_schema in s:
        name = field_schema.field_name
        validator_fn = _VALIDATOR_DISPATCH.get(field_schema.kind)

        if name not in data:
            if field_schema.required:
                violations.append(_violation(
                    name, "missing_field",
                    f"Required field '{name}' is missing from routing output",
                    expected=field_schema.kind, actual="(missing)",
                ))
            continue

        value = data[name]

        if value is None and field_schema.required:
            violations.append(_violation(
                name, "missing_field",
                f"Required field '{name}' is None",
                expected=field_schema.kind, actual="None",
            ))
            continue

        if value is None and not field_schema.required:
            continue  # optional + null = accepted

        if validator_fn is not None:
            field_violations = validator_fn(value, field_schema)
            violations.extend(field_violations)

    # ── Check for unknown keys (strict mode) ──
    if strict_keys:
        tolerated = _KNOWN_FIELDS | _TOLERATED_EXTRA_FIELDS
        for key in data:
            if key not in tolerated:
                violations.append(_violation(
                    key, "unknown_field",
                    f"Unknown field '{key}' in routing output",
                    expected=f"one of {sorted(tolerated)}", actual=repr(key),
                ))

    fields_checked = len(s)
    report = RoutingOutputValidationReport(
        passed=len(violations) == 0,
        violations=tuple(violations),
        fields_checked=fields_checked,
    )

    if not report.passed:
        raise RoutingOutputValidationError(report)

    return data


def is_valid_routing_output(data: dict[str, Any] | None, **kwargs: Any) -> bool:
    """Return ``True`` if *data* passes routing output schema validation.

    Convenience wrapper around :func:`validate_routing_output` that
    returns a boolean instead of raising.

    Args:
        data: The routing result dict to validate.
        **kwargs: Passed through to ``validate_routing_output``.

    Returns:
        ``True`` if validation passes, ``False`` otherwise.
    """
    try:
        validate_routing_output(data, **kwargs)
        return True
    except RoutingOutputValidationError:
        return False
