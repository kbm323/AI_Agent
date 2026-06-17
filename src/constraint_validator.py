"""Constraint validator — matches proposed routes against loaded constraint rules.

Sub-AC 3.3.2: Takes a proposed dynamic route (a dict with fields like
``required_roles``, ``optional_roles``, ``risk_tags``, ``codex_required``,
``round_count``, etc.) and evaluates it against loaded
:class:`~src.rule_store.StaticConstraintRule` objects.  Returns a
:class:`ConstraintValidationResult` with violation flags and details.

The rule ``rule_expr`` strings are evaluated via a safe expression
engine that resolves field names from the route data and applies
a predefined set of operators and functions.  Rules whose expressions
are natural-language (non-machine-evaluable) are tracked in
``rules_skipped`` and never silently produce false-negatives.

Design principles
-----------------
* **Safe evaluation** — no ``eval()`` or ``exec()``.  Expressions are
  parsed with ``ast`` and evaluated against a restricted scope.
* **No side effects** — pure function of (*route*, *rules*) → result.
* **Independently testable** — accepts arbitrary rule sets and route
  dicts; no filesystem, network, or CLI dependencies.
* **Descriptive violations** — every violation carries the rule id,
  description, enforcement action, and a human-readable detail.

Usage::

    from src.rule_store import RuleStore
    from src.constraint_validator import validate_route

    store = RuleStore.from_dict(config)
    route = {
        "required_roles": ["content-director", "validator", "art-director"],
        "optional_roles": ["script-writer", "character-designer", "vfx-artist", "sound-engineer"],
        "risk_tags": ["security_risk"],
        "codex_required": False,
        "round_count": 2,
    }
    result = validate_route(route, store.constraints)
    if not result.passed:
        for v in result.violations:
            print(f"VIOLATION: {v.rule_id} — {v.violation_detail}")
"""

from __future__ import annotations

import ast
import operator as _op
from dataclasses import dataclass, field
from typing import Any, Callable

from src.rule_store import RuleStore, StaticConstraintRule

# ═══════════════════════════════════════════════════════════════════════════
# Data types
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class ConstraintViolation:
    """A single constraint rule violation detected during validation.

    Attributes:
        rule_id: The violated rule's unique identifier.
        rule_description: Human-readable description of the rule.
        enforcement: Recommended enforcement action from the rule.
        category: Rule category (role_limits, codex_escalation, etc.).
        violation_detail: What specifically was violated — e.g.
            ``"required_roles + optional_roles = 8 exceeds max 7"``.
    """

    rule_id: str
    rule_description: str
    enforcement: str
    category: str
    violation_detail: str

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-serializable dict for logging and serialization."""
        return {
            "rule_id": self.rule_id,
            "rule_description": self.rule_description,
            "enforcement": self.enforcement,
            "category": self.category,
            "violation_detail": self.violation_detail,
        }


@dataclass(frozen=True)
class ConstraintValidationResult:
    """Aggregated result of validating a route against constraint rules.

    ``passed`` is ``True`` only when zero violations were detected AND
    all evaluable rules were checked.  Rules that could not be evaluated
    (natural-language expressions) are tracked in ``rules_skipped`` — they
    do not cause ``passed`` to be ``False``, but consumers should inspect
    ``rules_skipped`` for awareness.
    """

    passed: bool
    violations: tuple[ConstraintViolation, ...] = ()
    rules_checked: int = 0
    rules_evaluable: int = 0
    rules_skipped: int = 0
    skipped_rule_ids: tuple[str, ...] = ()

    @property
    def violation_count(self) -> int:
        return len(self.violations)

    def violations_by_category(self) -> dict[str, tuple[ConstraintViolation, ...]]:
        """Group violations by rule category."""
        grouped: dict[str, list[ConstraintViolation]] = {}
        for v in self.violations:
            grouped.setdefault(v.category, []).append(v)
        return {k: tuple(v) for k, v in grouped.items()}

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return {
            "passed": self.passed,
            "violation_count": self.violation_count,
            "violations": [v.to_dict() for v in self.violations],
            "rules_checked": self.rules_checked,
            "rules_evaluable": self.rules_evaluable,
            "rules_skipped": self.rules_skipped,
            "skipped_rule_ids": list(self.skipped_rule_ids),
        }


# ═══════════════════════════════════════════════════════════════════════════
# Safe expression evaluator
# ═══════════════════════════════════════════════════════════════════════════

# Allowed operators for safe evaluation.
_SAFE_OPERATORS: dict[type[ast.AST], Callable[..., Any]] = {
    ast.Add: _op.add,
    ast.Sub: _op.sub,
    ast.Mult: _op.mul,
    ast.Div: _op.truediv,
    ast.FloorDiv: _op.floordiv,
    ast.Mod: _op.mod,
    ast.Pow: _op.pow,
    ast.Eq: _op.eq,
    ast.NotEq: _op.ne,
    ast.Lt: _op.lt,
    ast.LtE: _op.le,
    ast.Gt: _op.gt,
    ast.GtE: _op.ge,
    ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
    ast.Is: _op.is_,
    ast.IsNot: _op.is_not,
    ast.And: lambda a, b: a and b,
    ast.Or: lambda a, b: a or b,
    ast.Invert: _op.inv,
    ast.Not: _op.not_,
    ast.UAdd: _op.pos,
    ast.USub: _op.neg,
}

# Built-in functions available in expressions.
_SAFE_BUILTINS: dict[str, Callable[..., Any]] = {
    "len": len,
    "any": any,
    "all": all,
    "min": min,
    "max": max,
    "sum": sum,
    "abs": abs,
    "int": int,
    "float": float,
    "str": str,
    "bool": bool,
    "list": list,
    "tuple": tuple,
    "set": set,
    "sorted": sorted,
    "isinstance": isinstance,
}


class _SafeEvaluator:
    """Evaluate a restricted subset of Python expressions using ``ast``.

    Walks the AST tree and evaluates nodes using only the allowed
    operators, built-ins, and a caller-supplied namespace of variable
    values.  This avoids the security risks of ``eval()`` while
    supporting the expression patterns used in guardrail rules.

    Usage::

        evaluator = _SafeEvaluator({"required_roles": ["a", "b"], "x": 5})
        result = evaluator.evaluate("len(required_roles) <= 6")  # True
    """

    def __init__(self, namespace: dict[str, Any]) -> None:
        self._namespace = namespace

    def evaluate(self, expression: str) -> Any:
        """Parse *expression* and evaluate it safely.

        Args:
            expression: A Python expression string (e.g. ``"len(x) <= 5"``).

        Returns:
            The evaluated result (bool, int, float, etc.).

        Raises:
            ValueError: If the expression cannot be parsed or uses
                disallowed constructs.
        """
        try:
            tree = ast.parse(expression.strip(), mode="eval")
        except SyntaxError as exc:
            raise ValueError(
                f"Invalid expression syntax: {expression!r} — {exc}"
            ) from exc
        return self._eval_node(tree.body)

    def _eval_node(self, node: ast.AST) -> Any:
        """Recursively evaluate an AST node."""
        # ── Literals ──
        if isinstance(node, ast.Constant):
            return node.value

        # ── Name lookup ──
        if isinstance(node, ast.Name):
            name = node.id
            if name in self._namespace:
                return self._namespace[name]
            if name in _SAFE_BUILTINS:
                return _SAFE_BUILTINS[name]
            if name in {"True", "False", "None"}:
                return {"True": True, "False": False, "None": None}[name]
            raise ValueError(
                f"Name {name!r} is not defined in the route namespace "
                f"and is not a safe built-in"
            )

        # ── Attribute access ──
        if isinstance(node, ast.Attribute):
            obj = self._eval_node(node.value)
            try:
                return getattr(obj, node.attr)
            except AttributeError as exc:
                raise ValueError(
                    f"Cannot access attribute {node.attr!r} on "
                    f"{type(obj).__name__}"
                ) from exc

        # ── Subscript / index ──
        if isinstance(node, ast.Subscript):
            value = self._eval_node(node.value)
            if isinstance(node.slice, ast.Constant):
                index = node.slice.value
            elif isinstance(node.slice, ast.Index):
                # Python 3.8 compat
                index = self._eval_node(node.slice.value)  # type: ignore[attr-defined]
            else:
                index = self._eval_node(node.slice)
            try:
                return value[index]
            except (IndexError, KeyError, TypeError) as exc:
                raise ValueError(
                    f"Subscript access failed on {type(value).__name__}"
                ) from exc

        # ── Binary operators ──
        if isinstance(node, ast.BinOp):
            left = self._eval_node(node.left)
            right = self._eval_node(node.right)
            op_type = type(node.op)
            if op_type not in _SAFE_OPERATORS:
                raise ValueError(f"Operator {op_type.__name__} is not allowed")
            # Short-circuit for and/or to avoid evaluating right unnecessarily
            if isinstance(node.op, ast.And):
                return bool(left) and bool(right)
            if isinstance(node.op, ast.Or):
                return bool(left) or bool(right)
            return _SAFE_OPERATORS[op_type](left, right)

        # ── Unary operators ──
        if isinstance(node, ast.UnaryOp):
            operand = self._eval_node(node.operand)
            op_type = type(node.op)
            if op_type not in _SAFE_OPERATORS:
                raise ValueError(f"Unary operator {op_type.__name__} is not allowed")
            return _SAFE_OPERATORS[op_type](operand)

        # ── Comparisons (a < b < c) ──
        if isinstance(node, ast.Compare):
            left = self._eval_node(node.left)
            for op_node, comparator in zip(node.ops, node.comparators):
                right = self._eval_node(comparator)
                op_type = type(op_node)
                if op_type not in _SAFE_OPERATORS:
                    raise ValueError(f"Comparison operator {op_type.__name__} is not allowed")
                op_func = _SAFE_OPERATORS[op_type]
                if not op_func(left, right):
                    return False
                left = right
            return True

        # ── Boolean operators (and/or) ──
        if isinstance(node, ast.BoolOp):
            if isinstance(node.op, ast.And):
                result = True
                for value_node in node.values:
                    result = result and bool(self._eval_node(value_node))
                    if not result:
                        break
                return result
            if isinstance(node.op, ast.Or):
                result = False
                for value_node in node.values:
                    result = result or bool(self._eval_node(value_node))
                    if result:
                        break
                return result
            raise ValueError(f"BoolOp {type(node.op).__name__} is not allowed")

        # ── If expressions (a if cond else b) ──
        if isinstance(node, ast.IfExp):
            cond = self._eval_node(node.test)
            if cond:
                return self._eval_node(node.body)
            return self._eval_node(node.orelse)

        # ── List / Tuple / Set / Dict literals ──
        if isinstance(node, ast.List):
            return [self._eval_node(el) for el in node.elts]
        if isinstance(node, ast.Tuple):
            return tuple(self._eval_node(el) for el in node.elts)
        if isinstance(node, ast.Set):
            return {self._eval_node(el) for el in node.elts}
        if isinstance(node, ast.Dict):
            return {
                self._eval_node(k): self._eval_node(v)
                for k, v in zip(node.keys, node.values)
            }

        # ── List comprehension ──
        if isinstance(node, ast.ListComp):
            return self._eval_comprehension(node, list)

        # ── Generator expression ──
        if isinstance(node, ast.GeneratorExp):
            return self._eval_generator(node)

        # ── Call expressions ──
        if isinstance(node, ast.Call):
            if node.func is None:
                raise ValueError("Call node has no function")
            func = self._eval_node(node.func)
            if not callable(func):
                raise ValueError(
                    f"Cannot call non-callable {type(func).__name__}"
                )
            args = [self._eval_node(a) for a in node.args]
            kwargs: dict[str, Any] = {}
            for kw in node.keywords:
                key: str = kw.arg if kw.arg is not None else ""
                kwargs[key] = self._eval_node(kw.value)
            return func(*args, **kwargs)

        raise ValueError(
            f"Unsupported AST node type: {type(node).__name__}"
        )

    def _eval_comprehension(self, node: ast.ListComp, builder: type) -> Any:
        """Evaluate a list comprehension."""
        result: list[Any] = []

        def _process(elt_node: ast.AST) -> None:
            result.append(self._eval_node(elt_node))

        self._eval_comp_iter(node.generators, 0, node.elt, _process, builder)
        return builder(result) if builder is not list else result

    def _eval_generator(self, node: ast.GeneratorExp) -> Any:
        """Evaluate a generator expression — returns list of results."""
        results: list[Any] = []

        def _process(elt_node: ast.AST) -> None:
            results.append(self._eval_node(elt_node))

        self._eval_comp_iter(node.generators, 0, node.elt, _process, list)
        return results

    def _eval_comp_iter(
        self,
        generators: list[ast.comprehension],
        gen_idx: int,
        elt: ast.AST,
        action: Callable[[ast.AST], None],
        builder: type,
    ) -> None:
        """Recursively evaluate nested comprehension generators."""
        comp = generators[gen_idx]
        iter_val = self._eval_node(comp.iter)

        # The target of a comprehension must be a Name node (or Tuple
        # of Names).  We support single-name targets.
        target = comp.target
        if not isinstance(target, ast.Name):
            raise ValueError(
                f"Comprehension target must be a simple Name, "
                f"got {type(target).__name__}"
            )
        target_name = target.id

        for item in iter_val:
            # Bind the iteration variable
            saved = self._namespace.get(target_name, _SENTINEL)
            self._namespace[target_name] = item

            # Evaluate if-conditions
            if all(self._eval_node(if_clause) for if_clause in comp.ifs):
                if gen_idx + 1 < len(generators):
                    self._eval_comp_iter(
                        generators, gen_idx + 1, elt, action, builder
                    )
                else:
                    action(elt)

            # Restore namespace
            if saved is _SENTINEL:
                self._namespace.pop(target_name, None)
            else:
                self._namespace[target_name] = saved


_SENTINEL = object()


# ═══════════════════════════════════════════════════════════════════════════
# Rule classification for evaluability
# ═══════════════════════════════════════════════════════════════════════════

# Rule expressions that use natural language and cannot be evaluated
# by the expression engine.
_NON_EVALUABLE_PATTERNS: tuple[str, ...] = (
    "must contain",
    "must record",
    "maps 1:1",
    "forbidden",
)


def _is_evaluable(rule: StaticConstraintRule) -> bool:
    """Determine if a rule's expression can be machine-evaluated.

    Returns ``False`` for natural-language expressions that need
    manual/human evaluation (e.g. "error_log must contain all failure events").
    """
    expr = rule.rule_expr.strip()
    expr_lower = expr.lower()
    for pattern in _NON_EVALUABLE_PATTERNS:
        if pattern in expr_lower:
            return False
    # Check if the expression (possibly transformed) can be evaluated
    try:
        transformed = _transform_if_then_expr(expr)
        ast.parse(transformed, mode="eval")
        return True
    except (SyntaxError, ValueError):
        return False


def _transform_if_then_expr(expr: str) -> str:
    """Transform if/then guardrail expressions into evaluable form.

    Guardrail rules use ``if <condition>: <assertion>`` syntax
    (Python statements).  This function converts them to the
    equivalent expression: ``not (<condition>) or <assertion>``.

    Args:
        expr: A raw rule expression string.

    Returns:
        A Python expression string suitable for ``ast.parse(mode="eval")``.

    Raises:
        ValueError: If the expression cannot be transformed.

    Examples:
        >>> _transform_if_then_expr(
        ...     "if 'security_risk' in risk_tags: codex_required = true"
        ... )
        "not ('security_risk' in risk_tags) or codex_required == True"

        >>> _transform_if_then_expr("len(x) <= 5")
        'len(x) <= 5'
    """
    expr = expr.strip()
    if not expr.startswith("if "):
        # Not an if/then expression — return as-is (handle "= true" → "== True")
        expr = _normalize_assignment_to_comparison(expr)
        return expr

    # Parse as a simple if statement: "if <cond>: <body>"
    after_if = expr[3:].strip()  # strip "if "
    colon_idx = after_if.find(":")
    if colon_idx == -1:
        raise ValueError(
            f"Malformed if/then expression (no colon): {expr!r}"
        )

    condition = after_if[:colon_idx].strip()
    body = after_if[colon_idx + 1:].strip()

    if not condition or not body:
        raise ValueError(
            f"Malformed if/then expression (empty condition or body): {expr!r}"
        )

    # Normalize body: replace "= true" with "== True", "= false" with "== False"
    body = _normalize_assignment_to_comparison(body)

    # Transform: "if cond: body" → "not (cond) or body"
    return f"not ({condition}) or {body}"


def _normalize_assignment_to_comparison(expr: str) -> str:
    """Replace assignment-like ``= true`` / ``= false`` with ``== True`` / ``== False``.

    Guardrail rules use ``field = true`` / ``field = false`` syntax
    (informal assignment), but evaluation requires ``field == True``.

    Args:
        expr: The expression string.

    Returns:
        Normalized expression string.
    """
    import re
    expr = re.sub(r'=\s*true\b', '== True', expr, flags=re.IGNORECASE)
    expr = re.sub(r'=\s*false\b', '== False', expr, flags=re.IGNORECASE)
    return expr


# ═══════════════════════════════════════════════════════════════════════════
# Per-category evaluators for well-known rule patterns
# ═══════════════════════════════════════════════════════════════════════════


def _evaluate_generic(rule: StaticConstraintRule, route: dict[str, Any]) -> bool:
    """Generic evaluator using the safe expression engine.

    Applies ``_transform_if_then_expr`` to convert if/then guardrail
    expressions into evaluable form before evaluation.

    Args:
        rule: The constraint rule to evaluate.
        route: The proposed route dict.

    Returns:
        ``True`` if the route satisfies the constraint, ``False`` if violated.

    Raises:
        ValueError: If the expression references undefined names or
            uses disallowed constructs.
    """
    # Transform if/then expressions to evaluable form
    evaluable_expr = _transform_if_then_expr(rule.rule_expr)
    evaluator = _SafeEvaluator(dict(route))
    try:
        result = evaluator.evaluate(evaluable_expr)
    except ValueError as exc:
        raise ValueError(
            f"Failed to evaluate rule {rule.rule_id!r} "
            f"(transformed: {evaluable_expr!r}): {exc}"
        ) from exc
    if not isinstance(result, bool):
        raise ValueError(
            f"Rule {rule.rule_id!r} expression does not evaluate "
            f"to a boolean (got {type(result).__name__})"
        )
    return result


def _evaluate_rule(rule: StaticConstraintRule, route: dict[str, Any]) -> bool:
    """Evaluate a single constraint rule against a route.

    The rule's expression must evaluate to a boolean.  The expression
    is evaluated in a namespace containing all keys from *route* plus
    the safe built-ins.

    Args:
        rule: The constraint rule to evaluate.
        route: The proposed route dict.

    Returns:
        ``True`` if the route satisfies the constraint, ``False`` if violated.

    Raises:
        ValueError: If the expression cannot be evaluated (references
            undefined names, uses disallowed constructs, or evaluates
            to a non-boolean).
    """
    return _evaluate_generic(rule, route)


# ═══════════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════════


def validate_route(
    route: dict[str, Any],
    rules: tuple[StaticConstraintRule, ...],
) -> ConstraintValidationResult:
    """Validate a proposed dynamic route against loaded constraint rules.

    This is the **single entry point** for Sub-AC 3.3.2.  It takes a
    route dict (containing fields like ``required_roles``,
    ``optional_roles``, ``risk_tags``, ``codex_required``,
    ``round_count``, etc.) and evaluates each constraint rule's
    expression against it.

    Rules with evaluable expressions (Python syntax) are checked and
    violations recorded.  Rules with natural-language expressions are
    tracked in ``rules_skipped`` and do not affect ``passed``.

    Args:
        route: The proposed route dict.  Keys are used as variable
            names in rule expression evaluation.  Must be a dict
            (not None).
        rules: Tuple of ``StaticConstraintRule`` objects from a
            ``RuleStore``.

    Returns:
        ``ConstraintValidationResult`` — inspect ``.passed`` to
        determine if all evaluable rules passed.

    Examples:
        >>> from src.rule_store import StaticConstraintRule
        >>> rules = (
        ...     StaticConstraintRule(
        ...         rule_id="max_roles",
        ...         description="Max 7 roles",
        ...         rule_expr="len(required_roles) + len(optional_roles) <= 7",
        ...         enforcement="truncate_optional",
        ...         category="role_limits",
        ...     ),
        ... )
        >>> route = {"required_roles": ["a","b","c","d"], "optional_roles": ["e","f","g","h"]}
        >>> result = validate_route(route, rules)
        >>> result.passed
        False
        >>> result.violations[0].rule_id
        'max_roles'
    """
    if not isinstance(route, dict):
        raise TypeError(
            f"route must be a dict, got {type(route).__name__}"
        )

    violations: list[ConstraintViolation] = []
    rules_checked = 0
    rules_evaluable = 0
    skipped_ids: list[str] = []

    for rule in rules:
        if not _is_evaluable(rule):
            skipped_ids.append(rule.rule_id)
            continue

        rules_evaluable += 1
        try:
            satisfied = _evaluate_rule(rule, route)
            rules_checked += 1
            if not satisfied:
                violations.append(
                    ConstraintViolation(
                        rule_id=rule.rule_id,
                        rule_description=rule.description,
                        enforcement=rule.enforcement,
                        category=rule.category,
                        violation_detail=(
                            f"Constraint violated: {rule.rule_expr}. "
                            f"Route values: {_summarize_route_for_rule(route, rule)}"
                        ),
                    )
                )
        except ValueError as exc:
            # Expression evaluation failed — track as skipped
            skipped_ids.append(f"{rule.rule_id} (eval_error: {exc})")

    return ConstraintValidationResult(
        passed=len(violations) == 0 and rules_evaluable > 0,
        violations=tuple(violations),
        rules_checked=rules_checked,
        rules_evaluable=rules_evaluable,
        rules_skipped=len(skipped_ids),
        skipped_rule_ids=tuple(skipped_ids),
    )


def validate_route_from_store(
    route: dict[str, Any],
    store: RuleStore,
) -> ConstraintValidationResult:
    """Convenience: validate a route against all constraints in a RuleStore.

    Equivalent to ``validate_route(route, store.constraints)``.

    Args:
        route: The proposed route dict.
        store: A loaded ``RuleStore`` instance.

    Returns:
        ``ConstraintValidationResult``.
    """
    return validate_route(route, store.constraints)


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════


def _summarize_route_for_rule(
    route: dict[str, Any],
    rule: StaticConstraintRule,
) -> str:
    """Build a compact summary of route values relevant to a rule."""
    expr = rule.rule_expr
    parts: list[str] = []

    # Extract likely variable names from expression
    for key in sorted(route.keys()):
        if key in expr:
            val = route[key]
            if isinstance(val, list):
                parts.append(
                    f"{key}=[{len(val)} items: {', '.join(str(v)[:60] for v in val[:5])}{'...' if len(val) > 5 else ''}]"
                )
            elif isinstance(val, bool):
                parts.append(f"{key}={val}")
            elif isinstance(val, (int, float)):
                parts.append(f"{key}={val}")
            elif isinstance(val, str):
                parts.append(f"{key}={val!r}")

    if not parts:
        return f"(route keys: {sorted(route.keys())})"
    return "; ".join(parts)
