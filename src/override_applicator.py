"""Override applicator — applies matching override rules to transform a route.

Sub-AC 3.3.3: Takes a proposed route dict and a set of
:class:`~src.rule_store.OverrideRule` objects, evaluates each rule's
condition against the route, and applies matching overrides to
transform a violating route into a compliant one.

The applicator works *with* the constraint validator (Sub-AC 3.3.2):
  1. ``validate_route()`` detects which constraints are violated.
  2. ``apply_overrides()`` transforms the route using matching override
     rules to resolve those violations.
  3. The caller then re-validates the transformed route.

Design principles:
  - **Safe condition evaluation** — reuses the same ``_SafeEvaluator``
    from ``constraint_validator`` for condition expression evaluation.
  - **Priority-ordered application** — higher-priority rules are applied
    later and can override earlier rules (last-write-wins on same field).
  - **No side effects** — pure function of (route, overrides) → result.
  - **Independently testable** — accepts arbitrary OverrideRule tuples
    and route dicts; zero filesystem/network/CLI dependencies.
  - **Idempotent** — applying the same overrides twice produces the
    same result.

Usage::

    from src.rule_store import RuleStore, OverrideRule
    from src.override_applicator import apply_overrides

    store = RuleStore.from_dict(config)
    route = {
        "required_roles": [...],
        "risk_tags": ["security_risk"],
        "codex_required": False,
    }
    result = apply_overrides(route, store.overrides)
    print(result.transformed_route["codex_required"])  # True
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from src.constraint_validator import _SafeEvaluator, _transform_if_then_expr
from src.rule_store import OverrideRule

# ═══════════════════════════════════════════════════════════════════════════
# Data types
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class OverrideChange:
    """A single field change applied by an override rule.

    Attributes:
        rule_id: The override rule that caused the change.
        field: The route field that was modified.
        old_value: The value before the override (``None`` if field was absent).
        new_value: The value after the override.
        action: The action name from the override rule.
    """

    rule_id: str
    field: str
    old_value: Any
    new_value: Any
    action: str

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return {
            "rule_id": self.rule_id,
            "field": self.field,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "action": self.action,
        }


@dataclass(frozen=True)
class OverrideApplicationResult:
    """Result of applying override rules to a route.

    ``transformed_route`` is a deep copy of the original with all
    matching override rules applied in priority order (lowest first,
    highest last so highest wins on the same field).

    ``changes`` records every field modification in application order
    for auditability.

    Attributes:
        original_route: The input route (unchanged).
        transformed_route: Deep copy of original with overrides applied.
        applied_rules: IDs of override rules whose conditions matched.
        skipped_rules: IDs of override rules whose conditions did not match.
        error_rules: IDs of override rules whose condition evaluation failed.
        changes: Ordered list of every field modification made.
        rules_total: Total number of override rules processed.
    """

    original_route: dict[str, Any]
    transformed_route: dict[str, Any]
    applied_rules: tuple[str, ...] = ()
    skipped_rules: tuple[str, ...] = ()
    error_rules: tuple[str, ...] = ()
    changes: tuple[OverrideChange, ...] = ()
    rules_total: int = 0

    @property
    def has_changes(self) -> bool:
        """True if at least one override was applied."""
        return len(self.changes) > 0

    @property
    def appled_count(self) -> int:
        """Number of successfully applied override rules."""
        return len(self.applied_rules)

    def changes_by_field(self) -> dict[str, tuple[OverrideChange, ...]]:
        """Group changes by field name."""
        grouped: dict[str, list[OverrideChange]] = {}
        for c in self.changes:
            grouped.setdefault(c.field, []).append(c)
        return {k: tuple(v) for k, v in grouped.items()}

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-serializable dict for logging/serialization."""
        return {
            "rules_total": self.rules_total,
            "applied_count": self.appled_count,
            "applied_rules": list(self.applied_rules),
            "skipped_rules": list(self.skipped_rules),
            "error_rules": list(self.error_rules),
            "has_changes": self.has_changes,
            "changes": [c.to_dict() for c in self.changes],
            "original_route_keys": sorted(self.original_route.keys()),
        }


# ═══════════════════════════════════════════════════════════════════════════
# Condition evaluation
# ═══════════════════════════════════════════════════════════════════════════


def _evaluate_condition(rule: OverrideRule, route: dict[str, Any]) -> bool:
    """Evaluate an override rule's condition against a route.

    Uses the same safe expression evaluator as the constraint validator.
    If/then expressions (``"if cond: body"``) are transformed to
    ``"not (cond) or body"`` form before evaluation.

    Args:
        rule: The override rule whose condition to evaluate.
        route: The route dict providing variable bindings.

    Returns:
        ``True`` if the condition matches (override should be applied),
        ``False`` otherwise.

    Raises:
        ValueError: If the condition references undefined names or
            uses disallowed constructs.
    """
    evaluable_expr = _transform_if_then_expr(rule.condition)
    evaluator = _SafeEvaluator(dict(route))
    result = evaluator.evaluate(evaluable_expr)
    if not isinstance(result, bool):
        raise ValueError(
            f"Override rule {rule.rule_id!r} condition does not "
            f"evaluate to a boolean (got {type(result).__name__})"
        )
    return result


# ═══════════════════════════════════════════════════════════════════════════
# Value coercion
# ═══════════════════════════════════════════════════════════════════════════


def _coerce_value(raw: str) -> Any:
    """Coerce a string target_value to its Python type.

    Override rules store target values as strings.  This function
    converts common literal forms:
      - ``"true"`` / ``"false"`` → ``True`` / ``False``
      - ``"P0"``, ``"P1"``, ``"P2"``, ``"P3"`` → kept as string
        (priority levels are strings)
      - Integer-looking strings → ``int``
      - Float-looking strings → ``float``
      - Everything else → kept as string

    Args:
        raw: The raw string value from the override rule.

    Returns:
        The coerced Python value.
    """
    stripped = raw.strip()
    lower = stripped.lower()

    # Booleans
    if lower == "true":
        return True
    if lower == "false":
        return False

    # None / null
    if lower in ("none", "null", ""):
        return None

    # Integers
    try:
        return int(stripped)
    except (ValueError, OverflowError):
        pass

    # Floats
    try:
        return float(stripped)
    except (ValueError, OverflowError):
        pass

    return stripped


# ═══════════════════════════════════════════════════════════════════════════
# Per-action applicators
# ═══════════════════════════════════════════════════════════════════════════


def _apply_set_field(
    route: dict[str, Any],
    rule: OverrideRule,
    changes: list[OverrideChange],
) -> None:
    """Set a single field to the target value.

    This is the default applicator for simple field overrides
    (e.g. ``force_codex_true`` → set ``codex_required = True``).

    Only records a change when the value actually differs from
    the target — this ensures idempotency.
    """
    field = rule.target_field
    new_val = _coerce_value(rule.target_value)
    old_val = route.get(field)

    # Idempotency: only apply if the value actually changes
    if old_val == new_val:
        return

    route[field] = new_val
    changes.append(
        OverrideChange(
            rule_id=rule.rule_id,
            field=field,
            old_value=old_val,
            new_value=new_val,
            action=rule.action,
        )
    )


def _apply_force_add(
    route: dict[str, Any],
    rule: OverrideRule,
    changes: list[OverrideChange],
) -> None:
    """Force-add a value to a list field (e.g. required_roles).

    Appends the target_value to the list if not already present.
    """
    field = rule.target_field
    new_item = rule.target_value.strip()

    old_list = list(route.get(field, []))
    if not isinstance(old_list, list):
        old_list = list(old_list) if hasattr(old_list, "__iter__") else []

    if new_item not in old_list:
        old_val = list(old_list)
        old_list.append(new_item)
        route[field] = old_list
        changes.append(
            OverrideChange(
                rule_id=rule.rule_id,
                field=field,
                old_value=old_val,
                new_value=old_list,
                action=rule.action,
            )
        )


def _apply_truncate_list(
    route: dict[str, Any],
    rule: OverrideRule,
    changes: list[OverrideChange],
) -> None:
    """Truncate a list field to a maximum length.

    The target_value is interpreted as the maximum number of items.
    Items beyond the limit are removed from the end.
    """
    field = rule.target_field
    max_items = int(rule.target_value.strip())

    old_list = list(route.get(field, []))
    if not isinstance(old_list, list):
        return

    if len(old_list) > max_items:
        old_val = list(old_list)
        new_list = old_list[:max_items]
        route[field] = new_list
        changes.append(
            OverrideChange(
                rule_id=rule.rule_id,
                field=field,
                old_value=old_val,
                new_value=new_list,
                action=rule.action,
            )
        )


# Action → applicator function mapping
_APPLICATORS: dict[str, Any] = {
    "force_codex_true": _apply_set_field,
    "force_add": _apply_force_add,
    "truncate": _apply_truncate_list,
    "set": _apply_set_field,
}


def _get_applicator(action: str):
    """Resolve an action name to an applicator function.

    Falls back to ``_apply_set_field`` for unknown actions since most
    override actions are simple field-set operations.
    """
    # Exact match
    if action in _APPLICATORS:
        return _APPLICATORS[action]

    # Prefix match (e.g. "force_codex_on_" → set field)
    for prefix, func in _APPLICATORS.items():
        if action.startswith(prefix):
            return func

    # Default: set field
    return _apply_set_field


# ═══════════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════════


def apply_overrides(
    route: dict[str, Any],
    overrides: tuple[OverrideRule, ...],
) -> OverrideApplicationResult:
    """Apply matching override rules to transform a route.

    This is the **single entry point** for Sub-AC 3.3.3.  It takes a
    route dict and a tuple of ``OverrideRule`` objects (sorted by
    priority, lowest first).  Each rule's condition is evaluated
    against the route; if it matches, the rule's action is applied
    to transform the route toward compliance.

    Rules are applied in order (lowest priority first).  When two
    rules target the same field, the higher-priority rule wins
    (last-write-wins).  The original route is never modified — the
    ``transformed_route`` is a deep copy.

    Args:
        route: The proposed route dict (e.g. from Qwen or static matcher).
            Must be a dict (not None).
        overrides: Tuple of ``OverrideRule`` objects, sorted by
            priority ascending (lowest first, highest last).

    Returns:
        ``OverrideApplicationResult`` — inspect ``.transformed_route``
        for the result and ``.changes`` for what was modified.

    Raises:
        TypeError: If ``route`` is not a dict.

    Examples:
        >>> from src.rule_store import OverrideRule
        >>> rules = (
        ...     OverrideRule(
        ...         rule_id="force_codex_on_security",
        ...         description="Security risk requires Codex",
        ...         condition="'security_risk' in risk_tags",
        ...         action="force_codex_true",
        ...         priority=10,
        ...         target_field="codex_required",
        ...         target_value="true",
        ...     ),
        ... )
        >>> route = {"risk_tags": ["security_risk"], "codex_required": False}
        >>> result = apply_overrides(route, rules)
        >>> result.transformed_route["codex_required"]
        True
        >>> result.appled_count
        1
    """
    if not isinstance(route, dict):
        raise TypeError(
            f"route must be a dict, got {type(route).__name__}"
        )

    # Work on a deep copy to preserve the original
    transformed = copy.deepcopy(route)
    changes: list[OverrideChange] = []
    applied: list[str] = []
    skipped: list[str] = []
    errors: list[str] = []

    for rule in overrides:
        try:
            matches = _evaluate_condition(rule, transformed)
        except (ValueError, SyntaxError, TypeError) as exc:
            errors.append(f"{rule.rule_id} (eval_error: {exc})")
            continue

        if not matches:
            skipped.append(rule.rule_id)
            continue

        # Apply the override
        applicator = _get_applicator(rule.action)
        applicator(transformed, rule, changes)
        applied.append(rule.rule_id)

    return OverrideApplicationResult(
        original_route=route,
        transformed_route=transformed,
        applied_rules=tuple(applied),
        skipped_rules=tuple(skipped),
        error_rules=tuple(errors),
        changes=tuple(changes),
        rules_total=len(overrides),
    )


# ═══════════════════════════════════════════════════════════════════════════
# Convenience: validate-then-apply pipeline
# ═══════════════════════════════════════════════════════════════════════════


def apply_overrides_for_violations(
    route: dict[str, Any],
    overrides: tuple[OverrideRule, ...],
    violation_fields: frozenset[str] | None = None,
) -> OverrideApplicationResult:
    """Apply only override rules targeting specific violation fields.

    When you know which fields are in violation (from the constraint
    validator), you can scope override application to only those fields.
    This prevents unnecessary overrides on compliant fields.

    Args:
        route: The proposed route dict.
        overrides: All available override rules.
        violation_fields: Set of field names that have violations.
            If ``None``, all overrides are evaluated (same as
            ``apply_overrides()``).

    Returns:
        ``OverrideApplicationResult`` with only the scoped overrides
        applied.
    """
    if violation_fields is None:
        return apply_overrides(route, overrides)

    # Filter to only rules targeting violated fields
    scoped = tuple(
        r for r in overrides if r.target_field in violation_fields
    )
    return apply_overrides(route, scoped)
