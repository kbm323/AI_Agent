"""Action descriptor validator for OpenClaw tool-use execution.

Sub-AC 15.1a: Validate tool-use action descriptors for required fields
(method, url, headers, body), URL format, and method constraints.
Designed as an independently runnable module with no filesystem,
network, or CLI dependencies — pure function of (descriptor dict) → result.

An action descriptor is a dict describing a tool-use action that OpenClaw
executes via HTTP/REST/MCP:

    {
        "method": "POST",
        "url": "https://api.example.com/v1/deploy",
        "headers": {"Authorization": "Bearer abc", "Content-Type": "application/json"},
        "body": {"target": "prod", "version": "1.2.0"},
        "timeout": 30.0,
    }

Supported HTTP methods: GET, POST, PUT, DELETE, PATCH, HEAD, OPTIONS.

The validator checks:
1. **Required fields** — ``method``, ``url``, ``headers``, ``body`` must be present.
2. **URL format** — valid URL scheme (http/https), non-empty host.
3. **Method constraints** — must be one of the allowed HTTP methods.
4. **Type validation** — ``headers`` must be a dict[str,str], ``body`` must be
   str/dict/bytes/None, ``timeout`` must be positive numeric if present.

Design principles
-----------------
* Pure function — no I/O, no side effects, trivially testable.
* Error collection — all violations are gathered, no early exit.
* Descriptive errors — each violation carries field name, error type,
  a human-readable message, and expected vs actual values.
* Follows the same dataclass pattern as ``field_format_validator``
  and ``constraint_validator`` modules.

Usage::

    from src.action_descriptor_validator import validate_action_descriptor

    descriptor = {
        "method": "POST",
        "url": "https://api.example.com/v1/deploy",
        "headers": {"Authorization": "Bearer token"},
        "body": {"key": "value"},
    }
    result = validate_action_descriptor(descriptor)
    if not result.passed:
        for err in result.errors:
            print(f"ERROR: {err.field_name} — {err.message}")
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, FrozenSet
from urllib.parse import urlparse

# ── Constants ─────────────────────────────────────────────────────────

VALID_HTTP_METHODS: FrozenSet[str] = frozenset({
    "GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS",
})
"""Allowed HTTP methods for action descriptors."""

VALID_URL_SCHEMES: FrozenSet[str] = frozenset({"http", "https"})
"""Allowed URL schemes."""

# ── Data types ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ActionDescriptorValidationError:
    """A single validation failure on an action descriptor field.

    Attributes:
        field_name: Name of the field that failed validation
            (``method``, ``url``, ``headers``, ``body``, ``timeout``,
            or empty string for top-level descriptor errors).
        error_type: Category of the error —
            ``missing``, ``wrong_type``, ``invalid_value``,
            ``bad_format``, ``empty_string``, ``unsupported_scheme``,
            ``invalid_url``, ``unsupported_method``, ``out_of_range``.
        message: Human-readable description of the failure.
        expected: What the validator expected.
        actual: What was actually received (type name or value repr).
    """

    field_name: str
    error_type: str
    message: str
    expected: str
    actual: str


@dataclass(frozen=True)
class ActionDescriptorValidationResult:
    """Aggregated result of action descriptor validation.

    ``passed`` is ``True`` only when **zero** errors were detected.
    The ``errors`` tuple can be iterated for logging or recovery.
    """

    passed: bool
    errors: tuple[ActionDescriptorValidationError, ...] = ()
    total_fields_checked: int = 0
    schema_version: str = "action-descriptor-validation.v1"

    @property
    def error_count(self) -> int:
        """Total number of validation errors."""
        return len(self.errors)

    def errors_by_field(self) -> dict[str, tuple[ActionDescriptorValidationError, ...]]:
        """Group errors by field name for targeted reporting."""
        grouped: dict[str, list[ActionDescriptorValidationError]] = {}
        for err in self.errors:
            grouped.setdefault(err.field_name, []).append(err)
        return {k: tuple(v) for k, v in grouped.items()}

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-serializable dict for logging and serialization."""
        return {
            "passed": self.passed,
            "error_count": self.error_count,
            "errors": [
                {
                    "field_name": e.field_name,
                    "error_type": e.error_type,
                    "message": e.message,
                    "expected": e.expected,
                    "actual": e.actual,
                }
                for e in self.errors
            ],
            "total_fields_checked": self.total_fields_checked,
            "schema_version": self.schema_version,
        }


# ── Helper: build error ────────────────────────────────────────────────


def _err(
    field_name: str,
    error_type: str,
    message: str,
    expected: str,
    actual: str,
) -> ActionDescriptorValidationError:
    """Convenience factory for a single validation error."""
    return ActionDescriptorValidationError(
        field_name=field_name,
        error_type=error_type,
        message=message,
        expected=expected,
        actual=actual,
    )


# ── Individual field validators ─────────────────────────────────────────


def _validate_method(method: Any) -> list[ActionDescriptorValidationError]:
    """Validate the ``method`` field.

    Must be a non-empty string from ``VALID_HTTP_METHODS``.
    """
    errors: list[ActionDescriptorValidationError] = []
    field = "method"

    if method is None:
        errors.append(
            _err(field, "missing",
                 "'method' is None; expected an HTTP method string.",
                 "one of " + ", ".join(sorted(VALID_HTTP_METHODS)),
                 "None"))
        return errors

    if not isinstance(method, str):
        errors.append(
            _err(field, "wrong_type",
                 f"'method' must be a string, got {type(method).__name__}.",
                 "string (HTTP method)",
                 type(method).__name__))
        return errors

    stripped = method.strip().upper()
    if stripped == "":
        errors.append(
            _err(field, "empty_string",
                 "'method' is empty or whitespace-only.",
                 "non-empty HTTP method string",
                 repr(method)))
        return errors

    if stripped not in VALID_HTTP_METHODS:
        errors.append(
            _err(field, "unsupported_method",
                 f"'method' value '{stripped}' is not a supported HTTP method.",
                 "one of " + ", ".join(sorted(VALID_HTTP_METHODS)),
                 repr(method)))
    return errors


def _validate_url(url: Any) -> list[ActionDescriptorValidationError]:
    """Validate the ``url`` field.

    Must be a non-empty string with a valid http/https scheme and
    a non-empty host (netloc).  Fragments are tolerated but noted
    in edge cases (not treated as errors since they are well-formed URLs).
    """
    errors: list[ActionDescriptorValidationError] = []
    field = "url"

    if url is None:
        errors.append(
            _err(field, "missing",
                 "'url' is None; expected a valid URL string.",
                 "valid http/https URL",
                 "None"))
        return errors

    if not isinstance(url, str):
        errors.append(
            _err(field, "wrong_type",
                 f"'url' must be a string, got {type(url).__name__}.",
                 "string (URL)",
                 type(url).__name__))
        return errors

    stripped = url.strip()
    if stripped == "":
        errors.append(
            _err(field, "empty_string",
                 "'url' is empty or whitespace-only.",
                 "non-empty URL string",
                 repr(url)))
        return errors

    # Parse and validate URL components
    try:
        parsed = urlparse(stripped)
    except ValueError as exc:
        errors.append(
            _err(field, "invalid_url",
                 f"'url' could not be parsed: {exc}.",
                 "valid http/https URL",
                 repr(stripped)[:120]))
        return errors

    # Scheme check
    if parsed.scheme == "":
        errors.append(
            _err(field, "invalid_url",
                 f"'url' '{stripped}' has no scheme. Expected http:// or https://.",
                 "http:// or https:// URL",
                 repr(stripped)[:120]))
        return errors

    if parsed.scheme not in VALID_URL_SCHEMES:
        errors.append(
            _err(field, "unsupported_scheme",
                 f"'url' scheme '{parsed.scheme}' is not supported. "
                 f"Only http and https are allowed.",
                 "http or https",
                 repr(parsed.scheme)))

    # Host (netloc) check
    if not parsed.netloc or parsed.netloc.strip() == "":
        errors.append(
            _err(field, "invalid_url",
                 f"'url' '{stripped}' has no host. "
                 f"Expected a fully-qualified URL with a hostname.",
                 "URL with hostname (e.g. https://api.example.com)",
                 repr(stripped)[:120]))

    return errors


def _validate_headers(headers: Any) -> list[ActionDescriptorValidationError]:
    """Validate the ``headers`` field.

    Must be a dict where all keys and values are strings.
    An empty dict is acceptable (some endpoints need no headers).
    """
    errors: list[ActionDescriptorValidationError] = []
    field = "headers"

    if headers is None:
        errors.append(
            _err(field, "missing",
                 "'headers' is None; expected a dict[str, str] (may be empty).",
                 "dict[str, str] (may be empty)",
                 "None"))
        return errors

    if not isinstance(headers, dict):
        errors.append(
            _err(field, "wrong_type",
                 f"'headers' must be a dict, got {type(headers).__name__}.",
                 "dict[str, str]",
                 type(headers).__name__))
        return errors

    # Validate keys and values are strings
    non_str_keys: list[str] = []
    non_str_values: list[str] = []
    for k, v in headers.items():
        if not isinstance(k, str):
            non_str_keys.append(repr(k))
        if not isinstance(v, str):
            non_str_values.append(f"{repr(k)}: {type(v).__name__}")

    if non_str_keys:
        errors.append(
            _err(field, "wrong_type",
                 f"'headers' contains {len(non_str_keys)} non-string key(s): "
                 f"{non_str_keys[:3]}{'…' if len(non_str_keys) > 3 else ''}. "
                 f"All header keys must be strings.",
                 "dict[str, str] — all keys must be strings",
                 f"keys with types: {non_str_keys[:3]}"))

    if non_str_values:
        errors.append(
            _err(field, "wrong_type",
                 f"'headers' contains {len(non_str_values)} non-string value(s): "
                 f"{non_str_values[:3]}{'…' if len(non_str_values) > 3 else ''}. "
                 f"All header values must be strings.",
                 "dict[str, str] — all values must be strings",
                 f"values with types: {non_str_values[:3]}"))

    return errors


def _validate_body(body: Any) -> list[ActionDescriptorValidationError]:
    """Validate the ``body`` field.

    Must be present (None is acceptable for GET/HEAD/OPTIONS requests).
    Acceptable types: str, dict, bytes, None.
    """
    errors: list[ActionDescriptorValidationError] = []
    field = "body"

    # body can legitimately be None (e.g. GET requests)
    # But the field *must be present* in the descriptor.
    # We detect absence at the top-level check, so here we just
    # validate the type if it's provided.

    if body is None:
        # None is valid — no error
        return errors

    if isinstance(body, (str, dict, bytes)):
        # All valid types
        return errors

    errors.append(
        _err(field, "wrong_type",
             f"'body' must be str, dict, bytes, or None, "
             f"got {type(body).__name__}.",
             "str | dict | bytes | None",
             type(body).__name__))
    return errors


def _validate_timeout(timeout: Any) -> list[ActionDescriptorValidationError]:
    """Validate the optional ``timeout`` field.

    If present, must be a positive int or float.
    """
    errors: list[ActionDescriptorValidationError] = []
    field = "timeout"

    if timeout is None:
        # Optional field — None is acceptable (use default)
        return errors

    if not isinstance(timeout, (int, float)):
        errors.append(
            _err(field, "wrong_type",
                 f"'timeout' must be a number, got {type(timeout).__name__}.",
                 "int | float (positive)",
                 type(timeout).__name__))
        return errors

    if isinstance(timeout, bool):
        # bool is a subclass of int, but it's not a valid timeout
        errors.append(
            _err(field, "wrong_type",
                 f"'timeout' must be a number, got bool (True/False). "
                 f"Use an int or float instead.",
                 "int | float (positive)",
                 "bool"))
        return errors

    if timeout <= 0:
        errors.append(
            _err(field, "out_of_range",
                 f"'timeout' must be positive, got {timeout}.",
                 "positive number (> 0)",
                 str(timeout)))

    return errors


# ── Top-level required field presence ───────────────────────────────────

_REQUIRED_FIELDS: tuple[str, ...] = ("method", "url", "headers", "body")
"""Fields that MUST be present in every action descriptor."""


def _check_required_fields(
    descriptor: dict[str, Any],
) -> list[ActionDescriptorValidationError]:
    """Check that all required top-level keys exist in *descriptor*.

    Returns:
        One error per missing required field.
    """
    errors: list[ActionDescriptorValidationError] = []
    for field in _REQUIRED_FIELDS:
        if field not in descriptor:
            errors.append(
                _err(field, "missing",
                     f"'{field}' is missing from the action descriptor. "
                     f"All descriptors must include: "
                     f"{', '.join(_REQUIRED_FIELDS)}.",
                     f"field '{field}' must be present",
                     "not present"))
    return errors


# ── Main public API ────────────────────────────────────────────────────


def validate_action_descriptor(
    descriptor: dict[str, Any],
) -> ActionDescriptorValidationResult:
    """Validate a tool-use action descriptor.

    Checks required fields (method, url, headers, body), URL format,
    HTTP method constraints, and optional timeout validity.

    Args:
        descriptor: A dict representing an action descriptor with keys
            ``method``, ``url``, ``headers``, ``body``, and optionally
            ``timeout``.

    Returns:
        An ``ActionDescriptorValidationResult`` with ``passed`` and
        ``errors`` fields.  ``passed`` is ``True`` only when zero
        errors are detected.

    Raises:
        TypeError: If *descriptor* is not a dict.

    Examples:
        >>> result = validate_action_descriptor({
        ...     "method": "POST",
        ...     "url": "https://api.example.com/v1/deploy",
        ...     "headers": {"Authorization": "Bearer token"},
        ...     "body": {"target": "prod"},
        ... })
        >>> result.passed
        True

        >>> result = validate_action_descriptor({
        ...     "method": "INVALID",
        ...     "url": "",
        ... })
        >>> result.passed
        False
        >>> result.error_count > 0
        True
    """
    if not isinstance(descriptor, dict):
        raise TypeError(
            f"descriptor must be a dict, got {type(descriptor).__name__}"
        )

    all_errors: list[ActionDescriptorValidationError] = []

    # 1. Check required fields
    all_errors.extend(_check_required_fields(descriptor))

    # 2. Validate individual fields (only if present, to avoid
    #    double-reporting missing fields)
    if "method" in descriptor:
        all_errors.extend(_validate_method(descriptor["method"]))
    if "url" in descriptor:
        all_errors.extend(_validate_url(descriptor["url"]))
    if "headers" in descriptor:
        all_errors.extend(_validate_headers(descriptor["headers"]))
    if "body" in descriptor:
        all_errors.extend(_validate_body(descriptor["body"]))
    if "timeout" in descriptor:
        all_errors.extend(_validate_timeout(descriptor["timeout"]))

    total_fields = len(descriptor)  # only keys actually present

    return ActionDescriptorValidationResult(
        passed=len(all_errors) == 0,
        errors=tuple(all_errors),
        total_fields_checked=total_fields,
    )
