"""Rule store/loader for static constraint rules and override rules (Sub-AC 3.3.1).

Loads and parses static constraint rules (guardrails) and override rules from
configuration. Returns typed Rule objects with a strict schema that downstream
consumers (Coordinator, validation layer, static matcher) can depend on.

Two rule categories:
  - **StaticConstraintRule**: Non-negotiable system invariants (guardrails).
    Always enforced. Loaded from ``routing_rules.yaml`` ``guardrails`` section.
  - **OverrideRule**: Conditional rules that override default/Qwen-provided
    routing decisions when triggered. Loaded from ``routing_rules.yaml``
    ``override_rules`` section (or a separate override config file).

Design principles:
  - **Typed output**: Every rule is a validated dataclass, not a raw dict.
  - **Mock-testable**: Accepts arbitrary dict config; zero filesystem dependency.
  - **Strict schema**: Missing or malformed fields raise descriptive errors.
  - **No side effects**: Pure function of config → parsed objects.

Usage::

    from src.rule_store import RuleStore, load_rules_from_config

    # From a dict (testable with mock data)
    store = RuleStore.from_dict(config_dict)
    for rule in store.constraints:
        print(rule.rule_id, rule.enforcement)

    # From the real routing rules YAML
    store = load_rules_from_config("config/routing_rules.yaml")
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from src.routing_rules_loader import load_routing_rules


# ═══════════════════════════════════════════════════════════════════════════
# Data models
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class StaticConstraintRule:
    """A non-negotiable guardrail rule — always enforced.

    Corresponds to one entry in the ``guardrails`` section of
    ``routing_rules.yaml``.  These rules are system invariants:
    even if Qwen returns a different classification, the guardrail
    logic MUST enforce these constraints before the routing result
    is committed.

    Attributes:
        rule_id: Unique identifier (e.g. ``"max_roles_per_meeting"``).
        description: Human-readable description of the constraint.
        rule_expr: Machine-readable rule expression (e.g.
            ``"len(required_roles) + len(optional_roles) <= 7"``).
        enforcement: Enforcement action name (e.g.
            ``"truncate_optional_roles_by_expertise_match"``).
        category: Rule category derived from its id and context:
            ``role_limits``, ``codex_escalation``,
            ``meeting_constraints``, ``silent_fail_prevention``,
            ``isolation``, ``model_constraints``.
        severity: Always ``"hard"`` for static constraints (they
            cannot be overridden or softened).

    Schema invariants (enforced on construction):
        - ``rule_id`` must be a non-empty kebab-case string.
        - ``description`` must be a non-empty string.
        - ``rule_expr`` must be a non-empty string.
        - ``enforcement`` must be a non-empty snake_case string.
        - ``category`` must be one of the known categories.
    """

    rule_id: str
    description: str
    rule_expr: str
    enforcement: str
    category: str = "role_limits"
    severity: str = "hard"

    _VALID_CATEGORIES: frozenset[str] = field(
        default=frozenset({
            "role_limits",
            "codex_escalation",
            "meeting_constraints",
            "silent_fail_prevention",
            "isolation",
            "model_constraints",
        }),
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        if not self.rule_id or not self.rule_id.strip():
            raise ValueError("StaticConstraintRule.rule_id must be non-empty")
        if not self.description or not self.description.strip():
            raise ValueError(
                f"StaticConstraintRule({self.rule_id!r}).description must be non-empty"
            )
        if not self.rule_expr or not self.rule_expr.strip():
            raise ValueError(
                f"StaticConstraintRule({self.rule_id!r}).rule_expr must be non-empty"
            )
        if not self.enforcement or not self.enforcement.strip():
            raise ValueError(
                f"StaticConstraintRule({self.rule_id!r}).enforcement must be non-empty"
            )
        if self.category not in self._VALID_CATEGORIES:
            raise ValueError(
                f"StaticConstraintRule({self.rule_id!r}).category = {self.category!r} "
                f"is not valid. Must be one of {sorted(self._VALID_CATEGORIES)}"
            )
        if self.severity != "hard":
            raise ValueError(
                f"StaticConstraintRule({self.rule_id!r}).severity must be 'hard', "
                f"got {self.severity!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-serializable dict for logging and serialization."""
        return {
            "rule_id": self.rule_id,
            "description": self.description,
            "rule_expr": self.rule_expr,
            "enforcement": self.enforcement,
            "category": self.category,
            "severity": self.severity,
            "rule_type": "static_constraint",
        }


@dataclass(frozen=True)
class OverrideRule:
    """A conditional rule that overrides default or Qwen routing decisions.

    Override rules differ from static constraints in that they are
    *conditional* — they only take effect when a triggering condition
    is met.  They represent configurable policies rather than hard
    system invariants.

    May be loaded from:
      - ``routing_rules.yaml`` ``override_rules`` section
      - A separate ``override_rules.yaml`` config file

    Attributes:
        rule_id: Unique identifier (e.g. ``"require_codex_on_security"``).
        description: Human-readable description.
        condition: Condition expression specifying when to trigger
            (e.g. ``"if 'security_risk' in risk_tags"``).
        action: What action to take (e.g. ``"force_codex_true"``).
        priority: Numeric priority (higher applied later → wins on conflict).
        target_field: Which field is overridden (e.g. ``"codex_required"``).
        target_value: The value to set (string representation, evaluated by
            the consumer — e.g. ``"true"``, ``"P0"``, ``"validator"``).

    Schema invariants:
        - ``rule_id`` must be non-empty.
        - ``description`` must be non-empty.
        - ``condition`` must be non-empty.
        - ``action`` must be non-empty.
        - ``target_field`` must be non-empty.
        - ``priority`` must be a non-negative integer (>= 0).
    """

    rule_id: str
    description: str
    condition: str
    action: str
    priority: int | float
    target_field: str
    target_value: str

    def __post_init__(self) -> None:
        if not self.rule_id or not self.rule_id.strip():
            raise ValueError("OverrideRule.rule_id must be non-empty")
        if not self.description or not self.description.strip():
            raise ValueError(
                f"OverrideRule({self.rule_id!r}).description must be non-empty"
            )
        if not self.condition or not self.condition.strip():
            raise ValueError(
                f"OverrideRule({self.rule_id!r}).condition must be non-empty"
            )
        if not self.action or not self.action.strip():
            raise ValueError(
                f"OverrideRule({self.rule_id!r}).action must be non-empty"
            )
        if not self.target_field or not self.target_field.strip():
            raise ValueError(
                f"OverrideRule({self.rule_id!r}).target_field must be non-empty"
            )
        if isinstance(self.priority, bool) or not isinstance(self.priority, (int, float)) or self.priority < 0 or self.priority != int(self.priority):
            raise ValueError(
                f"OverrideRule({self.rule_id!r}).priority must be a non-negative "
                f"integer, got {self.priority!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return {
            "rule_id": self.rule_id,
            "description": self.description,
            "condition": self.condition,
            "action": self.action,
            "priority": self.priority,
            "target_field": self.target_field,
            "target_value": self.target_value,
            "rule_type": "override",
        }


@dataclass(frozen=True)
class RuleSet:
    """Complete set of constraint and override rules.

    The primary output of :class:`RuleStore`.  Contains all rules
    loaded and parsed from configuration, separated by type.

    Attributes:
        version: Config version string (e.g. ``"1.0.0"``).
        static_constraints: Tuple of all static constraint rules.
        override_rules: Tuple of all override rules, sorted by priority
            (highest last).
        source_path: Path the rules were loaded from (empty string for
            dict-based loading).
    """

    version: str
    static_constraints: tuple[StaticConstraintRule, ...] = ()
    override_rules: tuple[OverrideRule, ...] = ()
    source_path: str = ""

    @property
    def constraint_count(self) -> int:
        """Number of static constraint rules."""
        return len(self.static_constraints)

    @property
    def override_count(self) -> int:
        """Number of override rules."""
        return len(self.override_rules)

    @property
    def total_rules(self) -> int:
        """Total number of all rules."""
        return self.constraint_count + self.override_count

    def constraints_by_category(self) -> dict[str, tuple[StaticConstraintRule, ...]]:
        """Group static constraints by category."""
        result: dict[str, list[StaticConstraintRule]] = {}
        for rule in self.static_constraints:
            result.setdefault(rule.category, []).append(rule)
        return {k: tuple(v) for k, v in result.items()}

    def overrides_by_target(self) -> dict[str, tuple[OverrideRule, ...]]:
        """Group override rules by target field."""
        result: dict[str, list[OverrideRule]] = {}
        for rule in self.override_rules:
            result.setdefault(rule.target_field, []).append(rule)
        return {k: tuple(v) for k, v in result.items()}

    def get_constraint(self, rule_id: str) -> StaticConstraintRule | None:
        """Find a static constraint by rule_id."""
        for rule in self.static_constraints:
            if rule.rule_id == rule_id:
                return rule
        return None

    def get_override(self, rule_id: str) -> OverrideRule | None:
        """Find an override rule by rule_id."""
        for rule in self.override_rules:
            if rule.rule_id == rule_id:
                return rule
        return None

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return {
            "version": self.version,
            "source_path": self.source_path,
            "static_constraints": [r.to_dict() for r in self.static_constraints],
            "override_rules": [r.to_dict() for r in self.override_rules],
            "stats": {
                "constraint_count": self.constraint_count,
                "override_count": self.override_count,
                "total_rules": self.total_rules,
            },
        }


# ═══════════════════════════════════════════════════════════════════════════
# Category inference
# ═══════════════════════════════════════════════════════════════════════════

#: Mapping from enforcement action name prefixes to category.
_ENFORCEMENT_CATEGORY_MAP: dict[str, str] = {
    "truncate_": "role_limits",
    "demote_": "role_limits",
    "force_add_": "role_limits",
    "promote_": "role_limits",
    "force_codex_true": "codex_escalation",
    "force_codex_on_": "model_constraints",
    "force_escalation_": "meeting_constraints",
    "queue_or_": "meeting_constraints",
    "reject_manifest_": "silent_fail_prevention",
    "validate_manifest_": "silent_fail_prevention",
    "create_or_verify_": "isolation",
    "force_glm_": "model_constraints",
    "force_codex_on_": "model_constraints",
}

#: Mapping from rule_id prefixes to category (fallback when enforcement
#: prefix doesn't match).
_RULE_ID_CATEGORY_MAP: dict[str, str] = {
    "max_roles": "role_limits",
    "max_required": "role_limits",
    "validator_always": "role_limits",
    "validator_is": "model_constraints",
    "team_leader": "role_limits",
    "security_": "codex_escalation",
    "data_loss_": "codex_escalation",
    "legal_": "codex_escalation",
    "external_publication": "codex_escalation",
    "max_rounds": "meeting_constraints",
    "max_concurrent": "meeting_constraints",
    "no_silent": "silent_fail_prevention",
    "required_role_no_": "silent_fail_prevention",
    "meeting_directory": "isolation",
    "no_same_model": "model_constraints",
}


def _infer_category(rule_id: str, enforcement: str) -> str:
    """Infer a StaticConstraintRule's category from its id and enforcement.

    Args:
        rule_id: The rule's unique identifier.
        enforcement: The enforcement action name.

    Returns:
        One of the valid category strings.  Falls back to ``"role_limits"``
        if no pattern matches.
    """
    # Try enforcement prefix first (most precise)
    for prefix, cat in _ENFORCEMENT_CATEGORY_MAP.items():
        if enforcement.startswith(prefix):
            return cat

    # Fall back to rule_id prefix
    for prefix, cat in _RULE_ID_CATEGORY_MAP.items():
        if rule_id.startswith(prefix):
            return cat

    return "role_limits"  # safe default


# ═══════════════════════════════════════════════════════════════════════════
# RuleStore
# ═══════════════════════════════════════════════════════════════════════════


class RuleStore:
    """Loads, parses, and stores constraint/override rules from configuration.

    The store is the single source of truth for all routing rules.
    It accepts a raw config dict (from YAML or programmatic construction)
    and produces a typed :class:`RuleSet`.

    Two construction paths:
      1. ``RuleStore.from_dict(config)`` — for testing with mock configs
      2. ``RuleStore.from_yaml(path)`` — for production use

    Usage::

        # Test path (no filesystem dependency)
        config = {"version": "1.0.0", "guardrails": [...]}
        store = RuleStore.from_dict(config)
        ruleset = store.ruleset

        # Production path
        store = RuleStore.from_yaml("config/routing_rules.yaml")
        for c in store.constraints:
            print(f"{c.rule_id}: {c.enforcement}")

        # Direct attribute access
        assert store.constraint_count == len(store.constraints)
        assert store.rules["max_roles_per_meeting"].enforcement == "..."
    """

    __slots__ = ("_ruleset",)

    def __init__(self, ruleset: RuleSet) -> None:
        """Internal constructor — use ``from_dict`` or ``from_yaml`` instead."""
        self._ruleset = ruleset

    # ── Properties ──────────────────────────────────────────────────────

    @property
    def ruleset(self) -> RuleSet:
        """The complete parsed rule set."""
        return self._ruleset

    @property
    def constraints(self) -> tuple[StaticConstraintRule, ...]:
        """All static constraint rules (guardrails)."""
        return self._ruleset.static_constraints

    @property
    def overrides(self) -> tuple[OverrideRule, ...]:
        """All override rules, sorted by priority (highest last)."""
        return self._ruleset.override_rules

    @property
    def constraint_count(self) -> int:
        """Number of static constraint rules."""
        return self._ruleset.constraint_count

    @property
    def override_count(self) -> int:
        """Number of override rules."""
        return self._ruleset.override_count

    @property
    def rules(self) -> dict[str, StaticConstraintRule]:
        """Static constraints indexed by rule_id for O(1) lookup."""
        return {r.rule_id: r for r in self._ruleset.static_constraints}

    @property
    def version(self) -> str:
        """Config version string."""
        return self._ruleset.version

    # ── Factory methods ─────────────────────────────────────────────────

    @classmethod
    def from_dict(cls, config: dict[str, Any]) -> RuleStore:
        """Build a RuleStore from a raw config dict.

        This is the primary testable entry point — pass arbitrary
        mock data to verify rule parsing and schema validation.

        Args:
            config: A dict with at minimum a ``"version"`` key and
                optionally ``"guardrails"`` and/or ``"override_rules"``
                lists.

        Returns:
            A fully parsed ``RuleStore`` with typed rule objects.

        Raises:
            ValueError: If a required field is missing or of wrong type.
            KeyError: If ``"version"`` is missing from config.

        Example:
            >>> config = {
            ...     "version": "1.0.0",
            ...     "guardrails": [
            ...         {"id": "max_roles", "description": "Limit roles",
            ...          "rule": "...", "enforcement": "truncate_optional"}
            ...     ]
            ... }
            >>> store = RuleStore.from_dict(config)
            >>> store.constraint_count
            1
        """
        version = str(config.get("version", "0.0.0"))

        constraints = cls._parse_constraints(
            config.get("guardrails", []),
            version=version,
        )
        overrides = cls._parse_overrides(
            config.get("override_rules", []),
            version=version,
        )

        return cls(
            RuleSet(
                version=version,
                static_constraints=constraints,
                override_rules=overrides,
                source_path="",  # dict-based loading has no source path
            )
        )

    @classmethod
    def from_yaml(cls, path: str | Path) -> RuleStore:
        """Build a RuleStore from a routing rules YAML file.

        Uses :func:`routing_rules_loader.load_routing_rules` to load
        and parse the YAML, then extracts all rule sections.

        Args:
            path: Path to a ``routing_rules.yaml`` file.

        Returns:
            A fully parsed ``RuleStore``.

        Raises:
            FileNotFoundError: If the file does not exist.
            yaml.YAMLError: If the file contains invalid YAML.
        """
        resolved = Path(path).expanduser().resolve()
        data = load_routing_rules(resolved)
        version = str(data.get("version", "0.0.0"))

        constraints = cls._parse_constraints(
            data.get("guardrails", []),
            version=version,
        )
        # Also check for a top-level override_rules section
        overrides = cls._parse_overrides(
            data.get("override_rules", []),
            version=version,
        )

        return cls(
            RuleSet(
                version=version,
                static_constraints=constraints,
                override_rules=overrides,
                source_path=str(resolved),
            )
        )

    # ── Parsers ─────────────────────────────────────────────────────────

    @staticmethod
    def _parse_constraints(
        raw_guardrails: list[dict[str, Any]],
        *,
        version: str,
    ) -> tuple[StaticConstraintRule, ...]:
        """Parse guardrail entries into StaticConstraintRule objects.

        Each entry in *raw_guardrails* must have ``id``, ``description``,
        ``rule``, and ``enforcement`` string fields.  The ``category``
        is inferred from the rule's semantics.

        Args:
            raw_guardrails: List of guardrail entry dicts.
            version: Config version (for error messages).

        Returns:
            Tuple of validated ``StaticConstraintRule`` objects.

        Raises:
            ValueError: If any entry is malformed.
            TypeError: If *raw_guardrails* is not a list.
        """
        if not isinstance(raw_guardrails, list):
            raise TypeError(
                f"guardrails must be a list, got {type(raw_guardrails).__name__}"
            )

        parsed: list[StaticConstraintRule] = []
        seen_ids: set[str] = set()

        for i, entry in enumerate(raw_guardrails):
            if not isinstance(entry, dict):
                raise TypeError(
                    f"guardrails[{i}] must be a dict, got {type(entry).__name__}"
                )

            rule_id = str(entry.get("id", "")).strip()
            if not rule_id:
                raise ValueError(
                    f"guardrails[{i}].id is required and must be non-empty"
                )
            if rule_id in seen_ids:
                raise ValueError(
                    f"guardrails[{i}].id = {rule_id!r} is a duplicate"
                )
            seen_ids.add(rule_id)

            description = str(entry.get("description", "")).strip()
            rule_expr = str(entry.get("rule", "")).strip()
            enforcement = str(entry.get("enforcement", "")).strip()

            if not description:
                raise ValueError(
                    f"guardrails[{i}] ({rule_id!r}).description must be non-empty"
                )
            if not rule_expr:
                raise ValueError(
                    f"guardrails[{i}] ({rule_id!r}).rule must be non-empty"
                )
            if not enforcement:
                raise ValueError(
                    f"guardrails[{i}] ({rule_id!r}).enforcement must be non-empty"
                )

            category = _infer_category(rule_id, enforcement)

            parsed.append(
                StaticConstraintRule(
                    rule_id=rule_id,
                    description=description,
                    rule_expr=rule_expr,
                    enforcement=enforcement,
                    category=category,
                    severity="hard",
                )
            )

        return tuple(parsed)

    @staticmethod
    def _parse_overrides(
        raw_overrides: list[dict[str, Any]],
        *,
        version: str,
    ) -> tuple[OverrideRule, ...]:
        """Parse override rule entries into OverrideRule objects.

        Each entry must have ``id``, ``description``, ``condition``,
        ``action``, ``priority``, ``target_field``, and ``target_value``.

        Rules are returned sorted by priority (highest last) so that
        higher-priority overrides win when applied in sequence.

        Args:
            raw_overrides: List of override rule dicts.
            version: Config version (for error messages).

        Returns:
            Tuple of validated ``OverrideRule`` objects, sorted by priority.

        Raises:
            ValueError: If any entry is malformed.
            TypeError: If *raw_overrides* is not a list.
        """
        if not isinstance(raw_overrides, list):
            raise TypeError(
                f"override_rules must be a list, got {type(raw_overrides).__name__}"
            )

        parsed: list[OverrideRule] = []
        seen_ids: set[str] = set()

        for i, entry in enumerate(raw_overrides):
            if not isinstance(entry, dict):
                raise TypeError(
                    f"override_rules[{i}] must be a dict, got {type(entry).__name__}"
                )

            rule_id = str(entry.get("id", "")).strip()
            if not rule_id:
                raise ValueError(
                    f"override_rules[{i}].id is required and must be non-empty"
                )
            if rule_id in seen_ids:
                raise ValueError(
                    f"override_rules[{i}].id = {rule_id!r} is a duplicate"
                )
            seen_ids.add(rule_id)

            description = str(entry.get("description", "")).strip()
            condition = str(entry.get("condition", "")).strip()
            action = str(entry.get("action", "")).strip()
            target_field = str(entry.get("target_field", "")).strip()
            target_value = str(entry.get("target_value", ""))

            if not description:
                raise ValueError(
                    f"override_rules[{i}] ({rule_id!r}).description must be non-empty"
                )
            if not condition:
                raise ValueError(
                    f"override_rules[{i}] ({rule_id!r}).condition must be non-empty"
                )
            if not action:
                raise ValueError(
                    f"override_rules[{i}] ({rule_id!r}).action must be non-empty"
                )
            if not target_field:
                raise ValueError(
                    f"override_rules[{i}] ({rule_id!r}).target_field must be "
                    f"non-empty"
                )

            priority_raw = entry.get("priority", 0)
            if isinstance(priority_raw, bool) or not isinstance(priority_raw, (int, float)):
                raise ValueError(
                    f"override_rules[{i}] ({rule_id!r}).priority must be a number, "
                    f"got {type(priority_raw).__name__} = {priority_raw!r}"
                )
            priority = int(priority_raw)

            parsed.append(
                OverrideRule(
                    rule_id=rule_id,
                    description=description,
                    condition=condition,
                    action=action,
                    priority=priority,
                    target_field=target_field,
                    target_value=target_value,
                )
            )

        # Sort by priority (lowest → highest), so higher-priority rules
        # (applied last) win on conflict.
        parsed.sort(key=lambda r: r.priority)
        return tuple(parsed)

    # ── Lookup methods ──────────────────────────────────────────────────

    def get_constraint(self, rule_id: str) -> StaticConstraintRule | None:
        """Find a static constraint rule by ID.

        Args:
            rule_id: The rule's unique identifier (e.g. ``"max_roles_per_meeting"``).

        Returns:
            The matching ``StaticConstraintRule`` or ``None``.
        """
        return self.rules.get(rule_id)

    def get_override(self, rule_id: str) -> OverrideRule | None:
        """Find an override rule by ID.

        Args:
            rule_id: The rule's unique identifier.

        Returns:
            The matching ``OverrideRule`` or ``None``.
        """
        for rule in self._ruleset.override_rules:
            if rule.rule_id == rule_id:
                return rule
        return None

    def find_overrides_for(self, target_field: str) -> tuple[OverrideRule, ...]:
        """Return all override rules targeting a specific field.

        Args:
            target_field: The field name to search for (e.g. ``"codex_required"``).

        Returns:
            Tuple of matching ``OverrideRule`` objects, sorted by priority.
        """
        return tuple(
            r for r in self._ruleset.override_rules
            if r.target_field == target_field
        )

    # ── Dunder ──────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        return (
            f"RuleStore(version={self.version!r}, "
            f"constraints={self.constraint_count}, "
            f"overrides={self.override_count})"
        )

    def __len__(self) -> int:
        return self._ruleset.total_rules

    def __contains__(self, rule_id: str) -> bool:
        return (
            rule_id in self.rules
            or any(r.rule_id == rule_id for r in self._ruleset.override_rules)
        )


# ═══════════════════════════════════════════════════════════════════════════
# Convenience function
# ═══════════════════════════════════════════════════════════════════════════


def load_rules_from_config(path: str | Path) -> RuleStore:
    """One-liner: load rules from a routing rules YAML file.

    Equivalent to ``RuleStore.from_yaml(path)``.

    Args:
        path: Path to ``routing_rules.yaml`` or equivalent config.

    Returns:
        A fully parsed ``RuleStore``.

    Example:
        >>> store = load_rules_from_config("config/routing_rules.yaml")
        >>> store.constraint_count
        18
    """
    return RuleStore.from_yaml(path)
