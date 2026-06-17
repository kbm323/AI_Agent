"""Structured fallback rules YAML loader — Sub-AC 3.2.1.

Parses, validates, and converts the YAML-defined fallback routing ruleset
into strongly-typed immutable dataclass objects with correct default value
population.

Pipeline:
    1. Load YAML (via :func:`routing_rules_loader.load_routing_rules`)
    2. Validate schema (via :func:`routing_rules_validator.validate_routing_rules`)
    3. Convert to structured :class:`FallbackRules` dataclass with defaults

The structured output exposes every section of ``routing_rules.yaml`` as
a typed attribute so downstream consumers (Coordinator, static matcher,
guardrail engine) can access rules without dict-key typos or missing-field
surprises.

Design principles:
- **Single entry point**: ``FallbackRules.from_yaml_file(path)`` for file,
  ``FallbackRules.from_dict(data)`` for already-parsed data.
- **Comprehensive coverage**: every top-level section is represented
  as a typed dataclass — no section is dropped.
- **Default population**: optional fields that have YAML-defined defaults
  are populated at conversion time, so consumers never check ``None``.
- **Immutability**: all dataclasses are ``frozen=True``, guaranteeing
  thread-safe read access and hashability.
- **Validation integration**: schema validation happens before conversion;
  invalid YAML never produces a partial structured object.

Usage::

    from src.fallback_rules_loader import FallbackRules

    rules = FallbackRules.from_yaml_file("config/routing_rules.yaml")
    print(rules.version)           # "1.0.0"
    print(len(rules.roles))        # 29
    print(rules.defaults.max_roles_per_meeting)  # 7

    for role in rules.roles:
        print(f"{role.role_id} → {role.team} ({role.role_type})")
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.routing_rules_loader import load_routing_rules
from src.routing_rules_validator import (
    RoutingRulesValidationError,
    validate_routing_rules,
)


# ═══════════════════════════════════════════════════════════════════════════
# Structured data types — one per YAML section
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class ModelSpec:
    """Model configuration for a single role.

    Attributes:
        provider: LLM provider name (e.g. ``"opencode-go"``).
        name: Primary model identifier (e.g. ``"qwen-max"``).
        fallback: Fallback model identifier (e.g. ``"deepseek-v3"``).
    """

    provider: str
    name: str
    fallback: str = ""


@dataclass(frozen=True)
class RoleSpec:
    """Structured representation of a single role from the role registry.

    Attributes:
        role_id: Unique kebab-case role identifier.
        display_name: Human-readable Korean display name.
        team: Team affiliation (kebab-case).
        role_type: Classification: ``leader``, ``worker``,
            ``validator``, ``executor``, ``coordinator``.
        persistent_bot: Whether this role has a persistent Discord bot.
        discord_name: Discord display name (empty string if not persistent).
        model: Model configuration (:class:`ModelSpec`).
        expertise_tags: Topic tags for routing.
        description: Free-text role description.
    """

    role_id: str
    display_name: str
    team: str
    role_type: str
    persistent_bot: bool
    discord_name: str
    model: ModelSpec
    expertise_tags: tuple[str, ...] = ()
    description: str = ""


@dataclass(frozen=True)
class TeamSpec:
    """Structured representation of a team definition.

    Attributes:
        team_id: Kebab-case team identifier.
        name: Human-readable Korean team name.
        display_emoji: Emoji character for the team.
        description: Free-text team description.
    """

    team_id: str
    name: str
    display_emoji: str
    description: str = ""


@dataclass(frozen=True)
class AgendaTypeSpec:
    """Keyword-based agenda type classification rule.

    Attributes:
        id: Kebab-case agenda type identifier.
        display_name: Human-readable Korean label.
        keywords: List of keyword groups (each group is a list of
            strings; a group matches if ANY keyword is found;
            the agenda type matches if ALL groups match).
        tags: Routing tags for this agenda type.
        risk_tags: Pre-assigned risk tags.
        required_roles: Role IDs required for quorum.
        optional_roles: Role IDs that may participate.
        validator_required: Whether GLM-5.1 validation is needed.
        codex_required: Whether Codex dual-validation is needed.
        note: Optional annotation (from YAML ``note`` field).
    """

    id: str
    display_name: str
    keywords: tuple[tuple[str, ...], ...] = ()
    tags: tuple[str, ...] = ()
    risk_tags: tuple[str, ...] = ()
    required_roles: tuple[str, ...] = ()
    optional_roles: tuple[str, ...] = ()
    validator_required: bool = True
    codex_required: bool = False
    note: str = ""


@dataclass(frozen=True)
class RiskPattern:
    """Risk detection pattern from ``risk_detection.patterns``.

    Attributes:
        risk_tag: Risk tag identifier (snake_case).
        severity: Severity level: ``low``, ``medium``, ``high``,
            ``critical``.
        keywords: Trigger keywords.
        auto_codex: Whether this pattern auto-triggers Codex escalation.
        requires_approval: Whether human-in-the-loop approval is
            required (defaults to ``False``).
        note: Optional annotation.
    """

    risk_tag: str
    severity: str = "medium"
    keywords: tuple[str, ...] = ()
    auto_codex: bool = False
    requires_approval: bool = False
    note: str = ""


@dataclass(frozen=True)
class CodexTrigger:
    """Codex escalation trigger rule from ``escalation_rules.codex_triggers``.

    Attributes:
        id: Trigger identifier.
        trigger_number: Numeric trigger index (1-7).
        description: Human-readable description.
        condition: The trigger condition dict (may contain
            ``any_risk_tag_in`` list or ``runtime: true``).
        action: Escalation action (e.g. ``set_codex_required_true``).
        note: Optional annotation.
    """

    id: str
    trigger_number: int = 0
    description: str = ""
    condition: dict[str, Any] = field(default_factory=dict)
    action: str = "set_codex_required_true"
    note: str = ""


@dataclass(frozen=True)
class PriorityRule:
    """Priority inference rule from ``priority_rules.inference``.

    Attributes:
        priority: Priority level: P0, P1, P2, P3.
        label: Human-readable Korean label.
        description: Free-text description.
        keywords: Trigger keywords.
    """

    priority: str = "P2"
    label: str = ""
    description: str = ""
    keywords: tuple[str, ...] = ()


@dataclass(frozen=True)
class Guardrail:
    """Hard safety guardrail from the ``guardrails`` section.

    Guardrails are non-negotiable system invariants that override
    even Qwen LLM output.

    Attributes:
        id: Guardrail identifier (kebab-case).
        description: Human-readable description.
        rule: The constraint rule (pseudocode / natural language).
        enforcement: Enforcement mechanism.
    """

    id: str
    description: str = ""
    rule: str = ""
    enforcement: str = ""


@dataclass(frozen=True)
class MatchingAlgorithm:
    """Keyword matching algorithm specification.

    Attributes:
        description: Algorithm overview.
        steps: Ordered list of step descriptions.
        language_support: Supported language codes.
        note: Optional annotation.
    """

    description: str = ""
    steps: tuple[str, ...] = ()
    language_support: tuple[str, ...] = ()
    note: str = ""


@dataclass(frozen=True)
class OutputSchema:
    """Output format contract from ``output_schema``.

    Attributes:
        description: Schema description.
        fields: Mapping of field name → type specifier string.
    """

    description: str = ""
    fields: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Metadata:
    """Routing rules metadata.

    Attributes:
        description: Free-text description of the ruleset.
        activated_when: Conditions that trigger fallback activation.
        primary_router: Name of the primary router.
        fallback_mode: Fallback classification mode.
    """

    description: str = ""
    activated_when: tuple[str, ...] = ()
    primary_router: str = ""
    fallback_mode: str = ""


@dataclass(frozen=True)
class Defaults:
    """System-wide defaults from the ``defaults`` section.

    All fields have sensible defaults matching ``routing_rules.yaml``
    so consumers can safely read any attribute without None-checks.

    Attributes:
        validator_required: Whether validator is required by default.
        validator_role_id: Default validator role ID.
        validator_model: Default validator model.
        codex_required: Whether Codex is required by default.
        codex_model: Default Codex model.
        max_roles_per_meeting: Max agents per meeting.
        max_required_roles: Max required roles per meeting.
        quorum_minimum_ratio: Minimum quorum ratio (0.0-1.0).
    """

    validator_required: bool = True
    validator_role_id: str = "validator"
    validator_model: str = "glm-5.1"
    codex_required: bool = False
    codex_model: str = "gpt-5.5"
    max_roles_per_meeting: int = 7
    max_required_roles: int = 6
    quorum_minimum_ratio: float = 0.67


# ═══════════════════════════════════════════════════════════════════════════
# Master container
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class FallbackRules:
    """Complete structured representation of routing_rules.yaml.

    This is the single entry point for consumers.  Every top-level
    YAML section is available as a typed attribute.  The object is
    immutable — safe for concurrent access and caching.

    Obtain an instance via:
        * ``FallbackRules.from_yaml_file(path)`` — file → structured
        * ``FallbackRules.from_dict(data)`` — pre-parsed dict → structured

    Attributes:
        version: Semantic version string (e.g. ``"1.0.0"``).
        metadata: Ruleset metadata (:class:`Metadata`).
        defaults: System-wide defaults (:class:`Defaults`).
        teams: Team definitions keyed by team_id (:class:`TeamSpec`).
        roles: Complete role registry — exactly 29 :class:`RoleSpec`.
        agenda_types: Agenda-type classification rules (:class:`AgendaTypeSpec`).
        risk_patterns: Risk detection patterns (:class:`RiskPattern`).
        codex_triggers: Codex escalation triggers (:class:`CodexTrigger`).
        priority_inference: Priority inference rules (:class:`PriorityRule`).
        default_priority: Default priority when no rule matches.
        guardrails: Hard safety guardrails (:class:`Guardrail`).
        matching_algorithm: Keyword matching algorithm spec (:class:`MatchingAlgorithm`).
        output_schema: Output format contract (:class:`OutputSchema`).
        raw: The original validated dict (for consumers that need
            untyped access — prefer typed attributes when possible).
    """

    version: str
    metadata: Metadata
    defaults: Defaults
    teams: dict[str, TeamSpec]
    roles: tuple[RoleSpec, ...]
    agenda_types: tuple[AgendaTypeSpec, ...]
    risk_patterns: tuple[RiskPattern, ...]
    codex_triggers: tuple[CodexTrigger, ...]
    priority_inference: tuple[PriorityRule, ...]
    default_priority: str = "P2"
    guardrails: tuple[Guardrail, ...] = ()
    matching_algorithm: MatchingAlgorithm = field(default_factory=MatchingAlgorithm)
    output_schema: OutputSchema = field(default_factory=OutputSchema)
    raw: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)

    # ── Factory methods ────────────────────────────────────────────────

    @classmethod
    def from_yaml_file(cls, path: str | Path) -> FallbackRules:
        """Load, validate, and convert a routing rules YAML file.

        Full pipeline: file → raw dict → validated dict → structured.

        Args:
            path: Absolute or relative path to routing_rules.yaml.

        Returns:
            A fully populated, validated :class:`FallbackRules` instance.

        Raises:
            FileNotFoundError: If the file does not exist.
            yaml.YAMLError: If the YAML is syntactically invalid.
            RoutingRulesValidationError: If the parsed YAML fails
                schema validation (missing fields, wrong types, etc.).
        """
        data = load_routing_rules(path)
        validated = validate_routing_rules(data)
        return cls.from_dict(validated)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FallbackRules:
        """Convert a validated routing rules dict into structured form.

        This method **does not** re-validate — it assumes *data* has
        already passed schema validation.  Use :meth:`from_yaml_file`
        for the full load+validate+convert pipeline.

        Args:
            data: A pre-validated dict matching the routing_rules
                schema (as produced by
                :func:`validate_routing_rules`).

        Returns:
            A fully populated :class:`FallbackRules` instance with
            all defaults filled.

        Raises:
            TypeError: If *data* is not a dict.
        """
        if not isinstance(data, dict):
            raise TypeError(
                f"data must be a dict, got {type(data).__name__}"
            )

        return cls(
            version=str(data.get("version", "1.0.0")),
            metadata=_convert_metadata(data.get("metadata", {})),
            defaults=_convert_defaults(data.get("defaults", {})),
            teams=_convert_teams(data.get("teams", {})),
            roles=_convert_roles(data.get("roles", [])),
            agenda_types=_convert_agenda_types(data.get("agenda_types", [])),
            risk_patterns=_convert_risk_patterns(
                data.get("risk_detection", {})
            ),
            codex_triggers=_convert_codex_triggers(
                data.get("escalation_rules", {})
            ),
            priority_inference=_convert_priority_inference(
                data.get("priority_rules", {})
            ),
            default_priority=_extract_default_priority(
                data.get("priority_rules", {})
            ),
            guardrails=_convert_guardrails(data.get("guardrails", [])),
            matching_algorithm=_convert_matching_algorithm(
                data.get("matching_algorithm", {})
            ),
            output_schema=_convert_output_schema(
                data.get("output_schema", {})
            ),
            raw=data,
        )


# ═══════════════════════════════════════════════════════════════════════════
# Internal converters — dict → structured dataclass
# ═══════════════════════════════════════════════════════════════════════════


def _convert_metadata(md: dict[str, Any]) -> Metadata:
    """Convert the ``metadata`` section dict to :class:`Metadata`."""
    activated = md.get("activated_when", [])
    if isinstance(activated, list):
        activated_when: tuple[str, ...] = tuple(
            str(x) for x in activated
        )
    else:
        activated_when = ()
    return Metadata(
        description=str(md.get("description", "")),
        activated_when=activated_when,
        primary_router=str(md.get("primary_router", "")),
        fallback_mode=str(md.get("fallback_mode", "")),
    )


def _convert_defaults(d: dict[str, Any]) -> Defaults:
    """Convert the ``defaults`` section dict to :class:`Defaults`.

    Every missing key is filled with the canonical default so
    downstream code never encounters ``None``.
    """
    return Defaults(
        validator_required=bool(d.get("validator_required", True)),
        validator_role_id=str(d.get("validator_role_id", "validator")),
        validator_model=str(d.get("validator_model", "glm-5.1")),
        codex_required=bool(d.get("codex_required", False)),
        codex_model=str(d.get("codex_model", "gpt-5.5")),
        max_roles_per_meeting=int(d.get("max_roles_per_meeting", 7)),
        max_required_roles=int(d.get("max_required_roles", 6)),
        quorum_minimum_ratio=float(d.get("quorum_minimum_ratio", 0.67)),
    )


def _convert_teams(teams: dict[str, Any]) -> dict[str, TeamSpec]:
    """Convert the ``teams`` section to a dict of :class:`TeamSpec`."""
    result: dict[str, TeamSpec] = {}
    for team_id, team_data in teams.items():
        if not isinstance(team_data, dict):
            continue
        result[team_id] = TeamSpec(
            team_id=team_id,
            name=str(team_data.get("name", team_id)),
            display_emoji=str(team_data.get("display_emoji", "")),
            description=str(team_data.get("description", "")),
        )
    return result


def _convert_roles(roles: list[dict[str, Any]]) -> tuple[RoleSpec, ...]:
    """Convert the ``roles`` list to a tuple of :class:`RoleSpec`."""
    result: list[RoleSpec] = []
    for role in roles:
        if not isinstance(role, dict):
            continue
        model_data = role.get("model", {})
        if not isinstance(model_data, dict):
            model_data = {}
        tags = role.get("expertise_tags", [])
        if not isinstance(tags, list):
            tags = []
        result.append(
            RoleSpec(
                role_id=str(role.get("role_id", "")),
                display_name=str(role.get("display_name", "")),
                team=str(role.get("team", "")),
                role_type=str(role.get("role_type", "")),
                persistent_bot=bool(role.get("persistent_bot", False)),
                discord_name=str(role.get("discord_name", "")),
                model=ModelSpec(
                    provider=str(model_data.get("provider", "")),
                    name=str(model_data.get("name", "")),
                    fallback=str(model_data.get("fallback", "")),
                ),
                expertise_tags=tuple(str(t) for t in tags),
                description=str(role.get("description", "")),
            )
        )
    return tuple(result)


def _convert_agenda_types(
    ats: list[dict[str, Any]],
) -> tuple[AgendaTypeSpec, ...]:
    """Convert the ``agenda_types`` list to a tuple of :class:`AgendaTypeSpec`."""
    result: list[AgendaTypeSpec] = []
    for at in ats:
        if not isinstance(at, dict):
            continue
        # Convert keyword groups: list of lists → tuple of tuples
        kw_raw = at.get("keywords", [])
        if isinstance(kw_raw, list):
            keywords: tuple[tuple[str, ...], ...] = tuple(
                tuple(str(k) for k in (g if isinstance(g, list) else []))
                for g in kw_raw
            )
        else:
            keywords = ()

        result.append(
            AgendaTypeSpec(
                id=str(at.get("id", "")),
                display_name=str(at.get("display_name", "")),
                keywords=keywords,
                tags=_safe_str_tuple(at.get("tags")),
                risk_tags=_safe_str_tuple(at.get("risk_tags")),
                required_roles=_safe_str_tuple(at.get("required_roles")),
                optional_roles=_safe_str_tuple(at.get("optional_roles")),
                validator_required=bool(at.get("validator_required", True)),
                codex_required=bool(at.get("codex_required", False)),
                note=str(at.get("note", "")),
            )
        )
    return tuple(result)


def _convert_risk_patterns(
    rd: dict[str, Any],
) -> tuple[RiskPattern, ...]:
    """Convert ``risk_detection.patterns`` to a tuple of :class:`RiskPattern`."""
    patterns = rd.get("patterns", [])
    if not isinstance(patterns, list):
        return ()
    result: list[RiskPattern] = []
    for pat in patterns:
        if not isinstance(pat, dict):
            continue
        result.append(
            RiskPattern(
                risk_tag=str(pat.get("risk_tag", "")),
                severity=str(pat.get("severity", "medium")),
                keywords=_safe_str_tuple(pat.get("keywords")),
                auto_codex=bool(pat.get("auto_codex", False)),
                requires_approval=bool(pat.get("requires_approval", False)),
                note=str(pat.get("note", "")),
            )
        )
    return tuple(result)


def _convert_codex_triggers(
    er: dict[str, Any],
) -> tuple[CodexTrigger, ...]:
    """Convert ``escalation_rules.codex_triggers`` to a tuple of :class:`CodexTrigger`."""
    triggers = er.get("codex_triggers", [])
    if not isinstance(triggers, list):
        return ()
    result: list[CodexTrigger] = []
    for tr in triggers:
        if not isinstance(tr, dict):
            continue
        condition = tr.get("condition", {})
        if not isinstance(condition, dict):
            condition = {}
        result.append(
            CodexTrigger(
                id=str(tr.get("id", "")),
                trigger_number=int(tr.get("trigger_number", 0)),
                description=str(tr.get("description", "")),
                condition=condition,
                action=str(tr.get("action", "set_codex_required_true")),
                note=str(tr.get("note", "")),
            )
        )
    return tuple(result)


def _convert_priority_inference(
    pr: dict[str, Any],
) -> tuple[PriorityRule, ...]:
    """Convert ``priority_rules.inference`` to a tuple of :class:`PriorityRule`."""
    inference = pr.get("inference", [])
    if not isinstance(inference, list):
        return ()
    result: list[PriorityRule] = []
    for rule in inference:
        if not isinstance(rule, dict):
            continue
        result.append(
            PriorityRule(
                priority=str(rule.get("priority", "P2")),
                label=str(rule.get("label", "")),
                description=str(rule.get("description", "")),
                keywords=_safe_str_tuple(rule.get("keywords")),
            )
        )
    return tuple(result)


def _extract_default_priority(pr: dict[str, Any]) -> str:
    """Extract the default priority from ``priority_rules.default``."""
    default = pr.get("default", "P2")
    if isinstance(default, str) and default in ("P0", "P1", "P2", "P3"):
        return default
    return "P2"


def _convert_guardrails(
    gr: list[dict[str, Any]],
) -> tuple[Guardrail, ...]:
    """Convert the ``guardrails`` list to a tuple of :class:`Guardrail`."""
    if not isinstance(gr, list):
        return ()
    result: list[Guardrail] = []
    for g in gr:
        if not isinstance(g, dict):
            continue
        result.append(
            Guardrail(
                id=str(g.get("id", "")),
                description=str(g.get("description", "")),
                rule=str(g.get("rule", "")),
                enforcement=str(g.get("enforcement", "")),
            )
        )
    return tuple(result)


def _convert_matching_algorithm(
    ma: dict[str, Any],
) -> MatchingAlgorithm:
    """Convert the ``matching_algorithm`` section to :class:`MatchingAlgorithm`."""
    steps = ma.get("steps", [])
    if not isinstance(steps, list):
        steps = []
    langs = ma.get("language_support", [])
    if not isinstance(langs, list):
        langs = []
    return MatchingAlgorithm(
        description=str(ma.get("description", "")),
        steps=tuple(str(s) for s in steps),
        language_support=tuple(str(l) for l in langs),
        note=str(ma.get("note", "")),
    )


def _convert_output_schema(
    os_: dict[str, Any],
) -> OutputSchema:
    """Convert the ``output_schema`` section to :class:`OutputSchema`."""
    fields = os_.get("fields", {})
    if not isinstance(fields, dict):
        fields = {}
    return OutputSchema(
        description=str(os_.get("description", "")),
        fields={str(k): str(v) for k, v in fields.items()},
    )


# ── Generic helper ────────────────────────────────────────────────────────


def _safe_str_tuple(value: Any) -> tuple[str, ...]:
    """Convert *value* to a tuple of strings, handling non-list input gracefully.

    Returns an empty tuple when *value* is not iterable (None, scalar, etc.).
    """
    if isinstance(value, (list, tuple)):
        return tuple(str(v) for v in value)
    return ()


# ═══════════════════════════════════════════════════════════════════════════
# Convenience — single-call loader
# ═══════════════════════════════════════════════════════════════════════════


def load_fallback_rules(
    path: str | Path | None = None,
) -> FallbackRules:
    """Load, validate, and convert routing rules from the default or given path.

    Convenience function for the common case: load
    ``config/routing_rules.yaml`` from the project root.  If no
    *path* is provided, the loader resolves the default location
    relative to the repository root.

    Args:
        path: Optional explicit path.  Defaults to
            ``<repo>/config/routing_rules.yaml``.

    Returns:
        A fully validated :class:`FallbackRules` instance.

    Raises:
        FileNotFoundError: If the file cannot be found.
        yaml.YAMLError: If the YAML is invalid.
        RoutingRulesValidationError: If schema validation fails.
    """
    if path is None:
        # Resolve relative to the repository root (two levels up from this file).
        default = (
            Path(__file__).resolve().parent.parent
            / "config"
            / "routing_rules.yaml"
        )
        path = default
    return FallbackRules.from_yaml_file(path)
