"""YAML file loader for the routing_rules.yaml fallback/guardrail configuration.

This module provides a single-purpose loader that reads the static
routing rules YAML file, parses it, and returns the raw parsed data
as a Python dict.  It is used by the Coordinator as a fallback router
when the Qwen LLM classification pipeline fails (timeout, parse error,
rate-limit, or CLI unavailability).

The loader targets Sub-AC 3.1a: load and parse routing_rules.yaml
at startup from the configured path, returning raw parsed data.
Errors are raised with **actionable** messages that indicate the
exact resolved path and suggest concrete fixes so the operator can
resolve the issue without reading the source code.

Error scenarios
---------------
* **FileNotFoundError** — file does not exist at the resolved path.
  Message includes the path and suggests creating the file or fixing
  the configuration.
* **yaml.YAMLError**    — file exists but contains invalid or malformed
  YAML.  Message includes the path, exact line and column number,
  and suggests checking for common YAML pitfalls (unclosed quotes,
  tab indentation, inconsistent spacing).
* **OSError**           — file exists but cannot be read (permissions,
  filesystem error).  Message includes the path and suggests checking
  file permissions or disk health.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def load_routing_rules(path: str | Path) -> dict[str, Any]:
    """Load and parse the routing rules YAML file.

    Reads the file at *path*, parses it with PyYAML's ``safe_load``, and
    returns the raw parsed data as a ``dict``.  No transformation or
    validation is performed — the caller receives the data exactly as
    parsed.

    The file is expected to follow the routing_rules.yaml schema: a
    top-level mapping with ``version``, ``metadata``, ``defaults``,
    ``teams``, ``roles``, and other sections defined in the Seed
    design document.

    All error messages are **actionable**: they include the resolved
    absolute path and concrete suggestions for fixing the problem
    (create the file, fix YAML syntax, check permissions, or update
    the configuration).

    Args:
        path: Absolute or relative path to the routing rules YAML file
              (typically ``config/routing_rules.yaml``).

    Returns:
        The parsed YAML content as a ``dict[str, Any]``.

    Raises:
        FileNotFoundError: If *path* does not exist or is not a file.
            The error message includes the resolved path and suggests
            creating the file or updating the configuration.
        yaml.YAMLError: If the file exists but contains invalid or
            malformed YAML that cannot be parsed.  The error message
            includes the file path and common YAML troubleshooting tips.
        OSError: If the file cannot be read for permissions or other
            I/O reasons.  The error message includes the path and
            suggests checking file permissions.

    Example:
        >>> rules = load_routing_rules("config/routing_rules.yaml")
        >>> rules["version"]
        '1.0.0'
        >>> len(rules["roles"])
        29
    """
    resolved = Path(path).expanduser().resolve()

    # ── File existence check ─────────────────────────────────────────
    if not resolved.is_file():
        raise FileNotFoundError(
            f"Routing rules file not found at: {resolved}\n"
            f"Action: Create the file at this path with valid YAML content, "
            f"or update your configuration to point to an existing "
            f"routing_rules.yaml file.\n"
            f"Expected path: {resolved}"
        )

    # ── YAML parsing ─────────────────────────────────────────────────
    try:
        with open(resolved, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        # ── Extract line/column from PyYAML's MarkedYAMLError ───────────
        location = ""
        try:
            pm = getattr(exc, "problem_mark", None)
            if pm is not None:
                location = f" at line {pm.line + 1}, column {pm.column + 1}"
        except (AttributeError, TypeError):
            pass
        raise yaml.YAMLError(
            f"Failed to parse routing rules YAML file: {resolved}{location}\n"
            f"Action: Fix the YAML syntax in this file. Common causes:\n"
            f"  - Unclosed or mismatched quotes (single/double)\n"
            f"  - Tab characters used for indentation (use spaces only)\n"
            f"  - Inconsistent indentation levels\n"
            f"  - Invalid YAML constructs (e.g. unquoted special characters)\n"
            f"Original error: {exc}"
        ) from exc
    except OSError as exc:
        raise OSError(
            f"Cannot read routing rules file at: {resolved}\n"
            f"Action: Check that the file has correct read permissions "
            f"(e.g., 'chmod 644 {resolved}') and that the filesystem "
            f"is healthy.\n"
            f"Original error: {exc}"
        ) from exc

    # ── Empty / comment-only file ────────────────────────────────────
    if data is None:
        # yaml.safe_load returns None for a file containing only
        # comments / whitespace — treat as empty rules.
        return {}

    # ── Top-level type check ─────────────────────────────────────────
    if not isinstance(data, dict):
        raise yaml.YAMLError(
            f"Routing rules file {resolved} must contain a YAML mapping "
            f"(top-level dict), but got a {type(data).__name__} instead.\n"
            f"Action: Ensure the file starts with key: value pairs at "
            f"the top level, not a YAML list (dash-prefixed items) or "
            f"a bare scalar value."
        )

    return data
