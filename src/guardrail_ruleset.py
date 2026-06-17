"""Guardrail ruleset: allowed routes, prohibited routes, and fallback mappings.

Sub-AC 3.2.1 — Loads, categorizes, and validates the guardrail ruleset from the
static routing configuration. Produces a structured :class:`GuardrailRuleset`
with three explicit categories:

  * **Allowed routes** — routing paths explicitly permitted by the system
    (e.g. which model→role assignments are valid, which meeting types can
    include which roles).

  * **Prohibited routes** — routing paths explicitly forbidden by hard
    constraints (e.g. role count limits, model restrictions, concurrency caps).

  * **Fallback route mappings** — mappings from primary routing paths to
    fallback paths when the primary fails or degrades (e.g. Qwen→static
    keyword matching, model fallback chains, Codex escalation triggers).

The ruleset is loaded from the existing ``routing_rules.yaml`` configuration
file.  Categorization is performed by analysing each guardrail's semantics
(enforcement action, rule expression, and description).

Design principles
-----------------
- **Immutable**: all dataclasses are ``frozen=True``.
- **Testable**: the loader accepts either a file path or a pre-parsed dict,
  so tests can inject arbitrary rule data without touching the filesystem.
- **Non-destructive**: no existing code is modified — this module reads
  and categorizes the already-existing guardrail definitions.
- **Backward-compatible**: consumers that already use :class:`FallbackRules`
  or :class:`RuleStore` are unaffected.

Usage::

    from src.guardrail_ruleset import GuardrailRuleset

    # From the real config file
    ruleset = GuardrailRuleset.from_yaml_file("config/routing_rules.yaml")
    print(len(ruleset.allowed_routes))    # e.g. 3
    print(len(ruleset.prohibited_routes)) # e.g. 9
    print(len(ruleset.fallback_mappings)) # e.g. 8

    # From a pre-parsed dict (testable)
    ruleset = GuardrailRuleset.from_dict(data)

    # Query by category
    for route in ruleset.prohibited_routes:
        print(f"{route.route_id}: {route.constraint} → {route.enforcement}")
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
class AllowedRoute:
    """A routing path explicitly permitted by the system.

    Attributes:
        route_id: Unique kebab-case identifier.
        description: Human-readable description of the permitted route.
        source_section: Which YAML section this originates from
            (``guardrails``, ``defaults``, ``agenda_types``, etc.).
        target: What this route allows (role, model, meeting config).
        enforcement: How the allowance is enforced.
    """

    route_id: str
    description: str
    source_section: str = "guardrails"
    target: str = ""
    enforcement: str = ""

    def __post_init__(self) -> None:
        if not self.route_id or not self.route_id.strip():
            raise ValueError("AllowedRoute.route_id must be non-empty")
        if not self.description or not self.description.strip():
            raise ValueError(
                f"AllowedRoute({self.route_id!r}).description must be non-empty"
            )

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return {
            "route_id": self.route_id,
            "description": self.description,
            "source_section": self.source_section,
            "target": self.target,
            "enforcement": self.enforcement,
            "route_type": "allowed",
        }


@dataclass(frozen=True)
class ProhibitedRoute:
    """A routing path explicitly forbidden by hard constraints.

    Attributes:
        route_id: Unique kebab-case identifier.
        description: Human-readable description of the prohibition.
        constraint: What constraint prohibits this route
            (e.g. ``"len(roles) <= 7"``).
        enforcement: How the prohibition is enforced
            (e.g. ``"truncate_optional_roles"``).
        severity: Always ``"hard"`` for prohibited routes — they
            cannot be overridden.
    """

    route_id: str
    description: str
    constraint: str = ""
    enforcement: str = ""
    severity: str = "hard"

    def __post_init__(self) -> None:
        if not self.route_id or not self.route_id.strip():
            raise ValueError("ProhibitedRoute.route_id must be non-empty")
        if not self.description or not self.description.strip():
            raise ValueError(
                f"ProhibitedRoute({self.route_id!r}).description must be non-empty"
            )
        if self.severity != "hard":
            raise ValueError(
                f"ProhibitedRoute({self.route_id!r}).severity must be 'hard', "
                f"got {self.severity!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return {
            "route_id": self.route_id,
            "description": self.description,
            "constraint": self.constraint,
            "enforcement": self.enforcement,
            "severity": self.severity,
            "route_type": "prohibited",
        }


@dataclass(frozen=True)
class FallbackRouteMapping:
    """A mapping from a primary routing path to a fallback path.

    Attributes:
        mapping_id: Unique kebab-case identifier.
        description: Human-readable description of the fallback mapping.
        primary_route: The primary routing path (what is tried first).
        fallback_route: The fallback routing path (what is used when
            the primary fails or is unavailable).
        trigger: What triggers the fallback (e.g. ``"qwen_timeout"``,
            ``"security_risk_detected"``).
        priority: Numeric priority — higher values apply later and
            win on conflict.
    """

    mapping_id: str
    description: str
    primary_route: str = ""
    fallback_route: str = ""
    trigger: str = ""
    priority: int = 0

    def __post_init__(self) -> None:
        if not self.mapping_id or not self.mapping_id.strip():
            raise ValueError("FallbackRouteMapping.mapping_id must be non-empty")
        if not self.description or not self.description.strip():
            raise ValueError(
                f"FallbackRouteMapping({self.mapping_id!r}).description "
                f"must be non-empty"
            )
        if not isinstance(self.priority, int) or self.priority < 0:
            raise ValueError(
                f"FallbackRouteMapping({self.mapping_id!r}).priority "
                f"must be a non-negative integer, got {self.priority!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return {
            "mapping_id": self.mapping_id,
            "description": self.description,
            "primary_route": self.primary_route,
            "fallback_route": self.fallback_route,
            "trigger": self.trigger,
            "priority": self.priority,
            "route_type": "fallback",
        }


@dataclass(frozen=True)
class GuardrailRuleset:
    """Complete guardrail ruleset with three route categories.

    Attributes:
        version: Config version string.
        allowed_routes: Tuple of explicitly permitted routing paths.
        prohibited_routes: Tuple of explicitly forbidden routing paths.
        fallback_mappings: Tuple of fallback route mappings.
        source_path: Path the ruleset was loaded from (empty for dict loading).
    """

    version: str
    allowed_routes: tuple[AllowedRoute, ...] = ()
    prohibited_routes: tuple[ProhibitedRoute, ...] = ()
    fallback_mappings: tuple[FallbackRouteMapping, ...] = ()
    source_path: str = ""

    @property
    def total_routes(self) -> int:
        """Total number of guardrail route entries."""
        return (
            len(self.allowed_routes)
            + len(self.prohibited_routes)
            + len(self.fallback_mappings)
        )

    @property
    def all_route_ids(self) -> tuple[str, ...]:
        """All route/mapping IDs across all three categories."""
        ids: list[str] = []
        ids.extend(r.route_id for r in self.allowed_routes)
        ids.extend(r.route_id for r in self.prohibited_routes)
        ids.extend(m.mapping_id for m in self.fallback_mappings)
        return tuple(ids)

    def get_by_id(self, route_id: str) -> AllowedRoute | ProhibitedRoute | FallbackRouteMapping | None:
        """Find any route/mapping by its ID across all categories."""
        for r in self.allowed_routes:
            if r.route_id == route_id:
                return r
        for r in self.prohibited_routes:
            if r.route_id == route_id:
                return r
        for m in self.fallback_mappings:
            if m.mapping_id == route_id:
                return m
        return None

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return {
            "version": self.version,
            "source_path": self.source_path,
            "allowed_routes": [r.to_dict() for r in self.allowed_routes],
            "prohibited_routes": [r.to_dict() for r in self.prohibited_routes],
            "fallback_mappings": [m.to_dict() for m in self.fallback_mappings],
            "stats": {
                "allowed_count": len(self.allowed_routes),
                "prohibited_count": len(self.prohibited_routes),
                "fallback_count": len(self.fallback_mappings),
                "total": self.total_routes,
            },
        }

    # ── Factory methods ────────────────────────────────────────────────

    @classmethod
    def from_yaml_file(cls, path: str | Path) -> GuardrailRuleset:
        """Load and categorize the guardrail ruleset from a YAML file.

        Args:
            path: Path to ``routing_rules.yaml``.

        Returns:
            A fully populated :class:`GuardrailRuleset`.

        Raises:
            FileNotFoundError: If the file does not exist.
            yaml.YAMLError: If the YAML is invalid.
        """
        resolved = Path(path).expanduser().resolve()
        data = load_routing_rules(resolved)
        return cls.from_dict(data, source_path=str(resolved))

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
        *,
        source_path: str = "",
    ) -> GuardrailRuleset:
        """Categorize guardrails from a pre-parsed routing rules dict.

        Extracts guardrail entries from the ``guardrails`` section (and
        any ``route_guardrails`` section if present) and categorizes each
        entry into allowed_routes, prohibited_routes, or fallback_mappings.

        Also derives fallback mappings from:
          - Model ``fallback`` fields in each role definition
          - ``escalation_rules.codex_triggers``
          - ``metadata.activated_when`` (fallback activation conditions)

        Args:
            data: Pre-parsed routing rules YAML dict.
            source_path: Optional source path for provenance tracking.

        Returns:
            A fully populated :class:`GuardrailRuleset`.

        Raises:
            ValueError: If a route entry is malformed.
        """
        if not isinstance(data, dict):
            raise TypeError(f"data must be a dict, got {type(data).__name__}")

        version = str(data.get("version", "0.0.0"))

        # ── 1. Parse explicit route_guardrails section if present ──
        allowed: list[AllowedRoute] = []
        prohibited: list[ProhibitedRoute] = []
        fallbacks: list[FallbackRouteMapping] = []

        rg = data.get("route_guardrails", {})
        if isinstance(rg, dict):
            allowed = _parse_allowed_routes(rg.get("allowed_routes", []))
            prohibited = _parse_prohibited_routes(rg.get("prohibited_routes", []))
            fallbacks = _parse_fallback_mappings(rg.get("fallback_mappings", []))

        # ── 2. Derive prohibited routes from guardrails section ──
        # Each guardrail that constrains/limits the system is a prohibition.
        raw_guardrails = data.get("guardrails", [])
        if isinstance(raw_guardrails, list):
            for g in raw_guardrails:
                if not isinstance(g, dict):
                    continue
                gid = str(g.get("id", "")).strip()
                if not gid:
                    continue
                desc = str(g.get("description", "")).strip()
                rule_expr = str(g.get("rule", "")).strip()
                enf = str(g.get("enforcement", "")).strip()

                # Determine category based on enforcement semantics
                category = _infer_guardrail_category(gid, enf)

                if category == "allowed":
                    allowed.append(
                        AllowedRoute(
                            route_id=gid,
                            description=desc,
                            source_section="guardrails",
                            target=_extract_target(desc),
                            enforcement=enf,
                        )
                    )
                elif category == "prohibited":
                    prohibited.append(
                        ProhibitedRoute(
                            route_id=gid,
                            description=desc,
                            constraint=rule_expr,
                            enforcement=enf,
                            severity="hard",
                        )
                    )
                elif category == "fallback":
                    fallbacks.append(
                        FallbackRouteMapping(
                            mapping_id=gid,
                            description=desc,
                            primary_route="default_behavior",
                            fallback_route=enf,
                            trigger=gid,
                            priority=0,
                        )
                    )

        # ── 3. Derive fallback mappings from model fallbacks ──
        roles = data.get("roles", [])
        if isinstance(roles, list):
            for role in roles:
                if not isinstance(role, dict):
                    continue
                model = role.get("model", {})
                if not isinstance(model, dict):
                    continue
                fallback_model = str(model.get("fallback", "")).strip()
                primary_model = str(model.get("name", "")).strip()
                role_id = str(role.get("role_id", "unknown")).strip()
                if fallback_model and primary_model:
                    mapping_id = f"model-fallback-{role_id}"
                    if not _has_mapping(fallbacks, mapping_id):
                        fallbacks.append(
                            FallbackRouteMapping(
                                mapping_id=mapping_id,
                                description=(
                                    f"Model fallback for {role_id}: "
                                    f"{primary_model} → {fallback_model}"
                                ),
                                primary_route=primary_model,
                                fallback_route=fallback_model,
                                trigger=f"{primary_model}_unavailable",
                                priority=0,
                            )
                        )

        # ── 4. Derive fallback mappings from escalation rules ──
        escalation = data.get("escalation_rules", {})
        if isinstance(escalation, dict):
            triggers = escalation.get("codex_triggers", [])
            if isinstance(triggers, list):
                for ct in triggers:
                    if not isinstance(ct, dict):
                        continue
                    ct_id = str(ct.get("id", "")).strip()
                    ct_desc = str(ct.get("description", "")).strip()
                    ct_action = str(ct.get("action", "")).strip()
                    if ct_id:
                        mapping_id = f"escalation-{ct_id}"
                        if not _has_mapping(fallbacks, mapping_id):
                            condition = ct.get("condition", {})
                            trigger_desc = _describe_condition(condition)
                            fallbacks.append(
                                FallbackRouteMapping(
                                    mapping_id=mapping_id,
                                    description=ct_desc,
                                    primary_route="glm_validation",
                                    fallback_route=ct_action,
                                    trigger=trigger_desc,
                                    priority=int(ct.get("trigger_number", 0)),
                                )
                            )

        # ── 5. Derive fallback mappings from activated_when ──
        metadata = data.get("metadata", {})
        if isinstance(metadata, dict):
            activated = metadata.get("activated_when", [])
            if isinstance(activated, list) and activated:
                mapping_id = "qwen-to-static-fallback"
                if not _has_mapping(fallbacks, mapping_id):
                    fallbacks.append(
                        FallbackRouteMapping(
                            mapping_id=mapping_id,
                            description=(
                                "Primary Qwen LLM router → static keyword-based "
                                "fallback classification"
                            ),
                            primary_route="qwen-llm-via-opencode-go",
                            fallback_route="static_keyword_based_classification",
                            trigger=", ".join(str(a) for a in activated),
                            priority=0,
                        )
                    )

        return cls(
            version=version,
            allowed_routes=tuple(allowed),
            prohibited_routes=tuple(prohibited),
            fallback_mappings=tuple(fallbacks),
            source_path=source_path,
        )


# ═══════════════════════════════════════════════════════════════════════════
# Internal helpers
# ═══════════════════════════════════════════════════════════════════════════


#: Guardrail IDs that represent ALLOWED routes (permissions, not constraints).
_ALLOWED_GUARDRAIL_IDS: frozenset[str] = frozenset({
    "validator_always_required",
    "team_leader_quorum",
})

#: Enforcement prefixes that indicate a PROHIBITED route.
_PROHIBITED_ENFORCEMENT_PREFIXES: tuple[str, ...] = (
    "truncate_",
    "demote_",
    "force_codex_true",
    "force_codex_on_",
    "force_escalation_",
    "queue_or_",
    "reject_manifest_",
    "validate_manifest_",
    "create_or_verify_",
    "force_glm_",
    "force_add_",
    "promote_",
)


def _infer_guardrail_category(rule_id: str, enforcement: str) -> str:
    """Infer whether a guardrail is an allowed route, prohibited route, or fallback.

    Heuristic:
      - Explicitly whitelisted IDs → ``"allowed"``
      - Enforcement starts with a known prohibition prefix → ``"prohibited"``
      - Otherwise → ``"fallback"``
    """
    if rule_id in _ALLOWED_GUARDRAIL_IDS:
        return "allowed"

    for prefix in _PROHIBITED_ENFORCEMENT_PREFIXES:
        if enforcement.startswith(prefix):
            return "prohibited"

    return "fallback"


def _extract_target(description: str) -> str:
    """Extract a short target description from a longer description string."""
    # Take first sentence or first 80 chars
    short = description.split(".")[0].strip()
    if len(short) > 80:
        short = short[:77] + "..."
    return short


def _has_mapping(
    mappings: list[FallbackRouteMapping],
    mapping_id: str,
) -> bool:
    """Check if a mapping with the given ID already exists."""
    return any(m.mapping_id == mapping_id for m in mappings)


def _describe_condition(condition: dict[str, Any]) -> str:
    """Convert a codex trigger condition dict to a human-readable string."""
    if not isinstance(condition, dict):
        return str(condition)
    if "any_risk_tag_in" in condition:
        tags = condition["any_risk_tag_in"]
        if isinstance(tags, list):
            return "risk_tags: " + ", ".join(str(t) for t in tags)
    if "runtime" in condition:
        return "runtime_evaluation"
    return ", ".join(f"{k}={v}" for k, v in condition.items())


def _parse_allowed_routes(raw: list[dict[str, Any]]) -> list[AllowedRoute]:
    """Parse allowed route entries from a route_guardrails.allowed_routes list."""
    result: list[AllowedRoute] = []
    seen: set[str] = set()
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise TypeError(
                f"allowed_routes[{i}] must be a dict, got {type(entry).__name__}"
            )
        rid = str(entry.get("id", "")).strip()
        if not rid:
            raise ValueError(f"allowed_routes[{i}].id is required")
        if rid in seen:
            raise ValueError(f"allowed_routes[{i}].id={rid!r} is duplicate")
        seen.add(rid)
        result.append(
            AllowedRoute(
                route_id=rid,
                description=str(entry.get("description", "")).strip(),
                source_section=str(entry.get("source_section", "route_guardrails")),
                target=str(entry.get("target", "")).strip(),
                enforcement=str(entry.get("enforcement", "")).strip(),
            )
        )
    return result


def _parse_prohibited_routes(raw: list[dict[str, Any]]) -> list[ProhibitedRoute]:
    """Parse prohibited route entries from route_guardrails.prohibited_routes."""
    result: list[ProhibitedRoute] = []
    seen: set[str] = set()
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise TypeError(
                f"prohibited_routes[{i}] must be a dict, got {type(entry).__name__}"
            )
        rid = str(entry.get("id", "")).strip()
        if not rid:
            raise ValueError(f"prohibited_routes[{i}].id is required")
        if rid in seen:
            raise ValueError(f"prohibited_routes[{i}].id={rid!r} is duplicate")
        seen.add(rid)
        result.append(
            ProhibitedRoute(
                route_id=rid,
                description=str(entry.get("description", "")).strip(),
                constraint=str(entry.get("constraint", "")).strip(),
                enforcement=str(entry.get("enforcement", "")).strip(),
                severity=str(entry.get("severity", "hard")).strip(),
            )
        )
    return result


def _parse_fallback_mappings(raw: list[dict[str, Any]]) -> list[FallbackRouteMapping]:
    """Parse fallback mapping entries from route_guardrails.fallback_mappings."""
    result: list[FallbackRouteMapping] = []
    seen: set[str] = set()
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise TypeError(
                f"fallback_mappings[{i}] must be a dict, got {type(entry).__name__}"
            )
        rid = str(entry.get("id", "")).strip()
        if not rid:
            raise ValueError(f"fallback_mappings[{i}].id is required")
        if rid in seen:
            raise ValueError(f"fallback_mappings[{i}].id={rid!r} is duplicate")
        seen.add(rid)
        result.append(
            FallbackRouteMapping(
                mapping_id=rid,
                description=str(entry.get("description", "")).strip(),
                primary_route=str(entry.get("primary_route", "")).strip(),
                fallback_route=str(entry.get("fallback_route", "")).strip(),
                trigger=str(entry.get("trigger", "")).strip(),
                priority=int(entry.get("priority", 0)),
            )
        )
    return result


# ═══════════════════════════════════════════════════════════════════════════
# Convenience function
# ═══════════════════════════════════════════════════════════════════════════


def load_guardrail_ruleset(path: str | Path | None = None) -> GuardrailRuleset:
    """Load the guardrail ruleset from the default or specified config.

    Args:
        path: Path to ``routing_rules.yaml``.  If ``None``, uses the
            default ``config/routing_rules.yaml`` relative to project root.

    Returns:
        A fully populated :class:`GuardrailRuleset`.

    Example:
        >>> ruleset = load_guardrail_ruleset()
        >>> ruleset.total_routes >= 30
        True
    """
    if path is None:
        path = Path(__file__).resolve().parent.parent / "config" / "routing_rules.yaml"
    return GuardrailRuleset.from_yaml_file(path)
