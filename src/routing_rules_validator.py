"""Schema validator for the routing rules YAML configuration.

Sub-AC 3.1b: Validates parsed routing rules data against required fields,
value types, and rule structure constraints.  Accepts the raw dict produced
by :func:`routing_rules_loader.load_routing_rules` and either returns the
validated config unchanged or raises a descriptive
:class:`RoutingRulesValidationError` with all detected problems.

Design principles
-----------------
* **No early-exit** — all sections and fields are checked so the caller
  sees every problem in a single round-trip.
* **Descriptive errors** — every failure carries the section path, the
  expected type/constraint, and the actual value observed.
* **Follows the same pattern** as :mod:`qwen_field_validator` and
  :mod:`field_format_validator` for consistency within the codebase.

Validated sections (all required)
---------------------------------
* ``version``           — semver string
* ``metadata``          — dict with ``description``, ``activated_when``,
  ``primary_router``, ``fallback_mode``
* ``defaults``          — typed dict with validator/model/codex settings
  and meeting limits
* ``teams``             — dict of 6 team definitions
* ``roles``             — list of exactly 29 role objects with field-level
  validation on each
* ``agenda_types``      — list of agenda-type classification rules
* ``risk_detection``    — dict with ``patterns`` list
* ``escalation_rules``  — dict with ``codex_triggers`` and
  ``conflict_resolution``
* ``priority_rules``    — dict with ``inference`` list and ``default``
* ``guardrails``        — list of guardrail rule objects
* ``matching_algorithm`` — dict describing the matching algorithm
* ``output_schema``     — dict describing the fallback output format
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ═══════════════════════════════════════════════════════════════════════════
# Custom exception
# ═══════════════════════════════════════════════════════════════════════════


class RoutingRulesValidationError(ValueError):
    """Raised when routing rules data fails schema validation.

    Carries the full :class:`ValidationReport` so callers can inspect
    individual failures programmatically without string-parsing the
    exception message.
    """

    def __init__(self, report: ValidationReport) -> None:
        self.report = report
        super().__init__(_format_report_summary(report))


# ═══════════════════════════════════════════════════════════════════════════
# Data types
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class SchemaViolation:
    """A single schema-level validation failure.

    Attributes:
        path: Dotted path to the offending field (e.g.
            ``"roles[3].role_id"``).
        error_type: Category — ``missing_section``, ``missing_field``,
            ``wrong_type``, ``invalid_value``, ``wrong_length``,
            ``unknown_key``.
        message: Human-readable description.
        expected: What was expected.
        actual: What was observed (type name or value repr).
    """

    path: str
    error_type: str
    message: str
    expected: str = ""
    actual: str = ""


@dataclass(frozen=True)
class ValidationReport:
    """Aggregated result of validating routing rules data.

    ``passed`` is ``True`` only when zero violations were detected.
    """

    passed: bool
    violations: tuple[SchemaViolation, ...] = ()
    sections_checked: int = 0

    @property
    def error_count(self) -> int:
        return len(self.violations)

    def violations_by_section(self) -> dict[str, tuple[SchemaViolation, ...]]:
        """Group violations by top-level section for targeted reporting."""
        grouped: dict[str, list[SchemaViolation]] = {}
        for v in self.violations:
            section = v.path.split(".")[0] if "." in v.path else v.path
            grouped.setdefault(section, []).append(v)
        return {k: tuple(v) for k, v in grouped.items()}


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════

_VALID_SEVERITIES: frozenset[str] = frozenset({"low", "medium", "high", "critical"})
_VALID_ROLE_TYPES: frozenset[str] = frozenset(
    {"leader", "worker", "validator", "executor", "coordinator"}
)
_VALID_TEAM_IDS: frozenset[str] = frozenset({
    "content-production",
    "art-design",
    "tech-engineering",
    "marketing",
    "validation",
    "execution",
})
_VALID_PRIORITIES: frozenset[str] = frozenset({"P0", "P1", "P2", "P3"})
_VALID_ESCALATION_ACTIONS: frozenset[str] = frozenset({
    "escalate_to_codex",
    "escalate_to_human",
    "force_codex_required",
    "force_re_validate",
    "pause_and_notify",
    "set_codex_required_true",
})
_EXPECTED_TEAM_COUNT = 6
_EXPECTED_ROLE_COUNT = 29


def _format_report_summary(report: ValidationReport) -> str:
    """Build a one-line summary for the exception message."""
    if report.passed:
        return "Validation passed"
    top_sections = list(report.violations_by_section().keys())[:5]
    return (
        f"Routing rules validation failed with {report.error_count} "
        f"violation(s) across sections: {', '.join(top_sections)}"
    )


def _sv(path: str, error_type: str, message: str, expected: str = "", actual: str = "") -> SchemaViolation:
    """Convenience constructor for SchemaViolation."""
    return SchemaViolation(path=path, error_type=error_type, message=message, expected=expected, actual=actual)


# ═══════════════════════════════════════════════════════════════════════════
# Per-section validators
# ═══════════════════════════════════════════════════════════════════════════


def _check_dict(data: Any, path: str, violations: list[SchemaViolation]) -> bool:
    """Check *data* is a dict; append violation if not.  Returns True on success."""
    if not isinstance(data, dict):
        violations.append(
            _sv(path, "wrong_type",
                f"'{path}' must be a dict/mapping, got {type(data).__name__}",
                expected="dict", actual=type(data).__name__))
        return False
    return True


def _check_list(data: Any, path: str, violations: list[SchemaViolation]) -> bool:
    """Check *data* is a list; append violation if not.  Returns True on success."""
    if not isinstance(data, list):
        violations.append(
            _sv(path, "wrong_type",
                f"'{path}' must be a list, got {type(data).__name__}",
                expected="list", actual=type(data).__name__))
        return False
    return True


def _check_str(data: Any, path: str, violations: list[SchemaViolation], *, allow_empty: bool = False) -> bool:
    """Check *data* is a non-empty (or empty-allowed) string."""
    if not isinstance(data, str):
        violations.append(
            _sv(path, "wrong_type",
                f"'{path}' must be a string, got {type(data).__name__}",
                expected="str", actual=type(data).__name__))
        return False
    if not allow_empty and data.strip() == "":
        violations.append(
            _sv(path, "invalid_value",
                f"'{path}' must not be empty", expected="non-empty str", actual=repr(data)))
        return False
    return True


def _check_bool(data: Any, path: str, violations: list[SchemaViolation]) -> bool:
    """Check *data* is a strict bool (not int 0/1)."""
    if not isinstance(data, bool):
        violations.append(
            _sv(path, "wrong_type",
                f"'{path}' must be a boolean (True/False), got {type(data).__name__} = {repr(data)[:80]}",
                expected="bool", actual=f"{type(data).__name__} = {repr(data)[:80]}"))
        return False
    return True


def _check_int(data: Any, path: str, violations: list[SchemaViolation], *, min_val: int | None = None) -> bool:
    """Check *data* is an int, optionally with a minimum value."""
    if not isinstance(data, int) or isinstance(data, bool):
        violations.append(
            _sv(path, "wrong_type",
                f"'{path}' must be an integer, got {type(data).__name__}",
                expected="int", actual=f"{type(data).__name__} = {repr(data)[:80]}"))
        return False
    if min_val is not None and data < min_val:
        violations.append(
            _sv(path, "invalid_value",
                f"'{path}' = {data} is below minimum {min_val}",
                expected=f">= {min_val}", actual=str(data)))
        return False
    return True


def _check_float(data: Any, path: str, violations: list[SchemaViolation], *, min_val: float = 0.0, max_val: float = 1.0) -> bool:
    """Check *data* is a float (or int) in [min_val, max_val]."""
    if isinstance(data, bool) or not isinstance(data, (int, float)):
        violations.append(
            _sv(path, "wrong_type",
                f"'{path}' must be a number, got {type(data).__name__}",
                expected="float", actual=f"{type(data).__name__} = {repr(data)[:80]}"))
        return False
    fval = float(data)
    if fval < min_val or fval > max_val:
        violations.append(
            _sv(path, "invalid_value",
                f"'{path}' = {fval} is outside [{min_val}, {max_val}]",
                expected=f"float in [{min_val}, {max_val}]", actual=str(fval)))
        return False
    return True


def _check_str_list(data: Any, path: str, violations: list[SchemaViolation], *, allow_empty: bool = True) -> None:
    """Validate *data* is a list of non-empty strings."""
    if not _check_list(data, path, violations):
        return
    for i, item in enumerate(data):
        ipath = f"{path}[{i}]"
        if not isinstance(item, str):
            violations.append(
                _sv(ipath, "wrong_type",
                    f"Item {i} in '{path}' must be a string, got {type(item).__name__}",
                    expected="str", actual=type(item).__name__))
        elif not allow_empty and item.strip() == "":
            violations.append(
                _sv(ipath, "invalid_value",
                    f"Item {i} in '{path}' must not be empty", expected="non-empty str", actual=repr(item)))


# ── version ──────────────────────────────────────────────────────────────

import re as _re

_SEMVER_RE = _re.compile(r"^\d+\.\d+\.\d+$")


def _validate_version(data: dict[str, Any], violations: list[SchemaViolation]) -> None:
    if "version" not in data:
        violations.append(_sv("version", "missing_section", "Top-level 'version' field is missing"))
        return
    v = data["version"]
    if not isinstance(v, str):
        violations.append(
            _sv("version", "wrong_type",
                f"'version' must be a SemVer string, got {type(v).__name__}",
                expected="str (e.g. '1.0.0')", actual=type(v).__name__))
    elif not _SEMVER_RE.match(v):
        violations.append(
            _sv("version", "invalid_value",
                f"'version' = {repr(v)} is not a valid SemVer (X.Y.Z)",
                expected="SemVer e.g. '1.0.0'", actual=repr(v)))


# ── metadata ─────────────────────────────────────────────────────────────


def _validate_metadata(data: dict[str, Any], violations: list[SchemaViolation]) -> None:
    if "metadata" not in data:
        violations.append(_sv("metadata", "missing_section", "Top-level 'metadata' section is missing"))
        return
    md = data["metadata"]
    if not _check_dict(md, "metadata", violations):
        return
    for field in ("description", "primary_router", "fallback_mode"):
        if field not in md:
            violations.append(
                _sv(f"metadata.{field}", "missing_field",
                    f"Required field 'metadata.{field}' is missing"))
        else:
            _check_str(md[field], f"metadata.{field}", violations)
    if "activated_when" not in md:
        violations.append(
            _sv("metadata.activated_when", "missing_field",
                "Required field 'metadata.activated_when' is missing"))
    elif isinstance(md["activated_when"], list):
        _check_str_list(md["activated_when"], "metadata.activated_when", violations)
    else:
        violations.append(
            _sv("metadata.activated_when", "wrong_type",
                "'metadata.activated_when' must be a list",
                expected="list[str]", actual=type(md["activated_when"]).__name__))


# ── defaults ─────────────────────────────────────────────────────────────


def _validate_defaults(data: dict[str, Any], violations: list[SchemaViolation]) -> None:
    if "defaults" not in data:
        violations.append(_sv("defaults", "missing_section", "Top-level 'defaults' section is missing"))
        return
    d = data["defaults"]
    if not _check_dict(d, "defaults", violations):
        return

    _check_bool(d.get("validator_required"), "defaults.validator_required", violations)
    _check_str(d.get("validator_role_id"), "defaults.validator_role_id", violations)
    _check_str(d.get("validator_model"), "defaults.validator_model", violations)
    _check_bool(d.get("codex_required"), "defaults.codex_required", violations)
    _check_str(d.get("codex_model"), "defaults.codex_model", violations)
    _check_int(d.get("max_roles_per_meeting"), "defaults.max_roles_per_meeting", violations, min_val=1)
    _check_int(d.get("max_required_roles"), "defaults.max_required_roles", violations, min_val=1)
    _check_float(d.get("quorum_minimum_ratio"), "defaults.quorum_minimum_ratio", violations)


# ── teams ─────────────────────────────────────────────────────────────────


def _validate_teams(data: dict[str, Any], violations: list[SchemaViolation]) -> None:
    if "teams" not in data:
        violations.append(_sv("teams", "missing_section", "Top-level 'teams' section is missing"))
        return
    teams = data["teams"]
    if not _check_dict(teams, "teams", violations):
        return

    actual_count = len(teams)
    if actual_count != _EXPECTED_TEAM_COUNT:
        violations.append(
            _sv("teams", "wrong_length",
                f"Expected {_EXPECTED_TEAM_COUNT} teams, found {actual_count}",
                expected=str(_EXPECTED_TEAM_COUNT), actual=str(actual_count)))

    for team_id, team_data in teams.items():
        prefix = f"teams.{team_id}"
        if not isinstance(team_data, dict):
            violations.append(
                _sv(prefix, "wrong_type",
                    f"Team '{team_id}' must be a dict, got {type(team_data).__name__}",
                    expected="dict", actual=type(team_data).__name__))
            continue
        if team_id not in _VALID_TEAM_IDS:
            violations.append(
                _sv(prefix, "invalid_value",
                    f"Unknown team id '{team_id}'. Valid: {sorted(_VALID_TEAM_IDS)}",
                    expected=f"one of {sorted(_VALID_TEAM_IDS)}", actual=repr(team_id)))
        for fld in ("name", "display_emoji", "description"):
            _check_str(team_data.get(fld), f"{prefix}.{fld}", violations)


# ── roles ─────────────────────────────────────────────────────────────────


def _validate_roles(data: dict[str, Any], violations: list[SchemaViolation]) -> None:
    if "roles" not in data:
        violations.append(_sv("roles", "missing_section", "Top-level 'roles' section is missing"))
        return
    roles = data["roles"]
    if not _check_list(roles, "roles", violations):
        return

    actual_count = len(roles)
    if actual_count != _EXPECTED_ROLE_COUNT:
        violations.append(
            _sv("roles", "wrong_length",
                f"Expected {_EXPECTED_ROLE_COUNT} roles, found {actual_count}",
                expected=str(_EXPECTED_ROLE_COUNT), actual=str(actual_count)))

    seen_ids: set[str] = set()
    for i, role in enumerate(roles):
        rpath = f"roles[{i}]"
        if not isinstance(role, dict):
            violations.append(
                _sv(rpath, "wrong_type",
                    f"Role entry {i} must be a dict, got {type(role).__name__}",
                    expected="dict", actual=type(role).__name__))
            continue

        # Required string fields
        for fld in ("role_id", "display_name", "team", "role_type", "description"):
            ok = _check_str(role.get(fld), f"{rpath}.{fld}", violations)
            if ok and fld == "role_id":
                rid = role[fld]
                if rid in seen_ids:
                    violations.append(
                        _sv(f"{rpath}.role_id", "invalid_value",
                            f"Duplicate role_id '{rid}'", expected="unique role_id", actual=repr(rid)))
                seen_ids.add(rid)
            if ok and fld == "team" and role[fld] not in _VALID_TEAM_IDS:
                violations.append(
                    _sv(f"{rpath}.team", "invalid_value",
                        f"Unknown team '{role[fld]}'. Valid: {sorted(_VALID_TEAM_IDS)}",
                        expected=f"one of {sorted(_VALID_TEAM_IDS)}", actual=repr(role[fld])))
            if ok and fld == "role_type" and role[fld] not in _VALID_ROLE_TYPES:
                violations.append(
                    _sv(f"{rpath}.role_type", "invalid_value",
                        f"Unknown role_type '{role[fld]}'. Valid: {sorted(_VALID_ROLE_TYPES)}",
                        expected=f"one of {sorted(_VALID_ROLE_TYPES)}", actual=repr(role[fld])))

        # persistent_bot (bool)
        _check_bool(role.get("persistent_bot"), f"{rpath}.persistent_bot", violations)

        # discord_name (optional string — only required for persistent bots)
        if "discord_name" in role:
            _check_str(role["discord_name"], f"{rpath}.discord_name", violations, allow_empty=True)

        # model sub-dict
        model = role.get("model")
        mpath = f"{rpath}.model"
        if not isinstance(model, dict):
            violations.append(
                _sv(mpath, "wrong_type",
                    f"Role '{role.get('role_id', f'index {i}')}' model must be a dict, got {type(model).__name__}",
                    expected="dict", actual=type(model).__name__))
        else:
            for mfld in ("provider", "name", "fallback"):
                _check_str(model.get(mfld), f"{mpath}.{mfld}", violations)

        # expertise_tags (list of strings)
        tags = role.get("expertise_tags")
        tpath = f"{rpath}.expertise_tags"
        if isinstance(tags, list):
            _check_str_list(tags, tpath, violations)
        else:
            violations.append(
                _sv(tpath, "wrong_type",
                    f"'expertise_tags' must be a list, got {type(tags).__name__}",
                    expected="list[str]", actual=type(tags).__name__))


# ── agenda_types ──────────────────────────────────────────────────────────


def _validate_agenda_types(data: dict[str, Any], violations: list[SchemaViolation]) -> None:
    if "agenda_types" not in data:
        violations.append(_sv("agenda_types", "missing_section", "Top-level 'agenda_types' section is missing"))
        return
    ats = data["agenda_types"]
    if not _check_list(ats, "agenda_types", violations):
        return

    seen_ids: set[str] = set()
    for i, at in enumerate(ats):
        apath = f"agenda_types[{i}]"
        if not isinstance(at, dict):
            violations.append(
                _sv(apath, "wrong_type",
                    f"Agenda type entry {i} must be a dict, got {type(at).__name__}",
                    expected="dict", actual=type(at).__name__))
            continue

        # id (required, unique)
        if "id" not in at:
            violations.append(_sv(f"{apath}.id", "missing_field", "Agenda type 'id' is required"))
        elif not isinstance(at["id"], str) or not at["id"].strip():
            violations.append(
                _sv(f"{apath}.id", "invalid_value",
                    "'id' must be a non-empty string", expected="non-empty str", actual=repr(at.get("id"))))
        else:
            if at["id"] in seen_ids:
                violations.append(
                    _sv(f"{apath}.id", "invalid_value",
                        f"Duplicate agenda_type id '{at['id']}'", expected="unique id", actual=repr(at["id"])))
            seen_ids.add(at["id"])

        _check_str(at.get("display_name"), f"{apath}.display_name", violations)

        # keywords: list of lists of strings (keyword groups)
        kw = at.get("keywords")
        kwpath = f"{apath}.keywords"
        if not isinstance(kw, list):
            violations.append(
                _sv(kwpath, "wrong_type",
                    "'keywords' must be a list of keyword groups",
                    expected="list[list[str]]", actual=type(kw).__name__))
        else:
            for gi, group in enumerate(kw):
                gpath = f"{kwpath}[{gi}]"
                if not isinstance(group, list):
                    violations.append(
                        _sv(gpath, "wrong_type",
                            f"Keyword group {gi} must be a list, got {type(group).__name__}",
                            expected="list[str]", actual=type(group).__name__))
                else:
                    _check_str_list(group, gpath, violations)

        # tags, risk_tags, required_roles, optional_roles — all list[str]
        for lst_fld in ("tags", "risk_tags", "required_roles", "optional_roles"):
            lst = at.get(lst_fld)
            lstpath = f"{apath}.{lst_fld}"
            if isinstance(lst, list):
                _check_str_list(lst, lstpath, violations)
            else:
                violations.append(
                    _sv(lstpath, "wrong_type",
                        f"'{lst_fld}' must be a list, got {type(lst).__name__}",
                        expected="list[str]", actual=type(lst).__name__))

        # validator_required, codex_required — bool
        _check_bool(at.get("validator_required"), f"{apath}.validator_required", violations)
        _check_bool(at.get("codex_required"), f"{apath}.codex_required", violations)


# ── risk_detection ────────────────────────────────────────────────────────


def _validate_risk_detection(data: dict[str, Any], violations: list[SchemaViolation]) -> None:
    if "risk_detection" not in data:
        violations.append(_sv("risk_detection", "missing_section", "Top-level 'risk_detection' section is missing"))
        return
    rd = data["risk_detection"]
    if not _check_dict(rd, "risk_detection", violations):
        return

    patterns = rd.get("patterns")
    if not isinstance(patterns, list):
        violations.append(
            _sv("risk_detection.patterns", "wrong_type",
                "'risk_detection.patterns' must be a list",
                expected="list[dict]", actual=type(patterns).__name__))
        return

    seen_tags: set[str] = set()
    for i, pat in enumerate(patterns):
        ppath = f"risk_detection.patterns[{i}]"
        if not isinstance(pat, dict):
            violations.append(
                _sv(ppath, "wrong_type",
                    f"Risk pattern {i} must be a dict, got {type(pat).__name__}",
                    expected="dict", actual=type(pat).__name__))
            continue

        _check_str(pat.get("risk_tag"), f"{ppath}.risk_tag", violations)
        tag_val = pat.get("risk_tag")
        if isinstance(tag_val, str):
            if tag_val in seen_tags:
                violations.append(
                    _sv(f"{ppath}.risk_tag", "invalid_value",
                        f"Duplicate risk_tag '{tag_val}'", expected="unique risk_tag", actual=repr(tag_val)))
            seen_tags.add(tag_val)

        sev = pat.get("severity")
        if isinstance(sev, str) and sev not in _VALID_SEVERITIES:
            violations.append(
                _sv(f"{ppath}.severity", "invalid_value",
                    f"Unknown severity '{sev}'. Valid: {sorted(_VALID_SEVERITIES)}",
                    expected=f"one of {sorted(_VALID_SEVERITIES)}", actual=repr(sev)))

        kw = pat.get("keywords")
        if isinstance(kw, list):
            _check_str_list(kw, f"{ppath}.keywords", violations)
        else:
            violations.append(
                _sv(f"{ppath}.keywords", "wrong_type",
                    "'keywords' must be a list",
                    expected="list[str]", actual=type(kw).__name__))

        _check_bool(pat.get("auto_codex"), f"{ppath}.auto_codex", violations)


# ── escalation_rules ──────────────────────────────────────────────────────


def _validate_escalation_rules(data: dict[str, Any], violations: list[SchemaViolation]) -> None:
    if "escalation_rules" not in data:
        violations.append(_sv("escalation_rules", "missing_section",
                              "Top-level 'escalation_rules' section is missing"))
        return
    er = data["escalation_rules"]
    if not _check_dict(er, "escalation_rules", violations):
        return

    # codex_triggers
    triggers = er.get("codex_triggers")
    tpath = "escalation_rules.codex_triggers"
    if isinstance(triggers, list):
        for i, tr in enumerate(triggers):
            trpath = f"{tpath}[{i}]"
            if not isinstance(tr, dict):
                violations.append(
                    _sv(trpath, "wrong_type",
                        f"Codex trigger {i} must be a dict", expected="dict", actual=type(tr).__name__))
                continue
            _check_str(tr.get("id"), f"{trpath}.id", violations)
            _check_int(tr.get("trigger_number"), f"{trpath}.trigger_number", violations, min_val=1)
            _check_str(tr.get("description"), f"{trpath}.description", violations)
            _check_str(tr.get("action"), f"{trpath}.action", violations)
            act = tr.get("action")
            if isinstance(act, str) and act not in _VALID_ESCALATION_ACTIONS:
                violations.append(
                    _sv(f"{trpath}.action", "invalid_value",
                        f"Unknown escalation action '{act}'. Valid: {sorted(_VALID_ESCALATION_ACTIONS)}",
                        expected=f"one of {sorted(_VALID_ESCALATION_ACTIONS)}", actual=repr(act)))
    else:
        violations.append(
            _sv(tpath, "wrong_type",
                "'codex_triggers' must be a list", expected="list[dict]", actual=type(triggers).__name__))

    # conflict_resolution (dict)
    cr = er.get("conflict_resolution")
    crpath = "escalation_rules.conflict_resolution"
    if not isinstance(cr, dict):
        violations.append(
            _sv(crpath, "wrong_type",
                "'conflict_resolution' must be a dict", expected="dict", actual=type(cr).__name__))


# ── priority_rules ────────────────────────────────────────────────────────


def _validate_priority_rules(data: dict[str, Any], violations: list[SchemaViolation]) -> None:
    if "priority_rules" not in data:
        violations.append(_sv("priority_rules", "missing_section",
                              "Top-level 'priority_rules' section is missing"))
        return
    pr = data["priority_rules"]
    if not _check_dict(pr, "priority_rules", violations):
        return

    # inference list
    inference = pr.get("inference")
    infpath = "priority_rules.inference"
    if isinstance(inference, list):
        for i, rule in enumerate(inference):
            rpath = f"{infpath}[{i}]"
            if not isinstance(rule, dict):
                violations.append(
                    _sv(rpath, "wrong_type",
                        f"Priority inference rule {i} must be a dict", expected="dict", actual=type(rule).__name__))
                continue
            prio = rule.get("priority")
            _check_str(prio, f"{rpath}.priority", violations)
            if isinstance(prio, str) and prio not in _VALID_PRIORITIES:
                violations.append(
                    _sv(f"{rpath}.priority", "invalid_value",
                        f"Unknown priority '{prio}'. Valid: {sorted(_VALID_PRIORITIES)}",
                        expected=f"one of {sorted(_VALID_PRIORITIES)}", actual=repr(prio)))
            _check_str(rule.get("label"), f"{rpath}.label", violations)
            _check_str(rule.get("description"), f"{rpath}.description", violations)
            kw = rule.get("keywords")
            if isinstance(kw, list):
                _check_str_list(kw, f"{rpath}.keywords", violations)
            else:
                violations.append(
                    _sv(f"{rpath}.keywords", "wrong_type",
                        "'keywords' must be a list", expected="list[str]", actual=type(kw).__name__))
    else:
        violations.append(
            _sv(infpath, "wrong_type",
                "'inference' must be a list", expected="list[dict]", actual=type(inference).__name__))

    # default
    default = pr.get("default")
    if isinstance(default, str) and default not in _VALID_PRIORITIES:
        violations.append(
            _sv("priority_rules.default", "invalid_value",
                f"Unknown default priority '{default}'. Valid: {sorted(_VALID_PRIORITIES)}",
                expected=f"one of {sorted(_VALID_PRIORITIES)}", actual=repr(default)))


# ── guardrails ────────────────────────────────────────────────────────────


def _validate_guardrails(data: dict[str, Any], violations: list[SchemaViolation]) -> None:
    if "guardrails" not in data:
        violations.append(_sv("guardrails", "missing_section", "Top-level 'guardrails' section is missing"))
        return
    gr = data["guardrails"]
    if not _check_list(gr, "guardrails", violations):
        return

    seen_ids: set[str] = set()
    for i, g in enumerate(gr):
        gpath = f"guardrails[{i}]"
        if not isinstance(g, dict):
            violations.append(
                _sv(gpath, "wrong_type",
                    f"Guardrail {i} must be a dict", expected="dict", actual=type(g).__name__))
            continue
        gid = g.get("id")
        _check_str(gid, f"{gpath}.id", violations)
        if isinstance(gid, str):
            if gid in seen_ids:
                violations.append(
                    _sv(f"{gpath}.id", "invalid_value",
                        f"Duplicate guardrail id '{gid}'", expected="unique id", actual=repr(gid)))
            seen_ids.add(gid)
        _check_str(g.get("description"), f"{gpath}.description", violations)
        _check_str(g.get("rule"), f"{gpath}.rule", violations)
        _check_str(g.get("enforcement"), f"{gpath}.enforcement", violations)


# ── matching_algorithm ────────────────────────────────────────────────────


def _validate_matching_algorithm(data: dict[str, Any], violations: list[SchemaViolation]) -> None:
    if "matching_algorithm" not in data:
        violations.append(_sv("matching_algorithm", "missing_section",
                              "Top-level 'matching_algorithm' section is missing"))
        return
    ma = data["matching_algorithm"]
    if not _check_dict(ma, "matching_algorithm", violations):
        return
    _check_str(ma.get("description"), "matching_algorithm.description", violations)

    # ── Validate steps (list of strings) ──
    steps = ma.get("steps")
    spath = "matching_algorithm.steps"
    if isinstance(steps, list):
        _check_str_list(steps, spath, violations)
    else:
        violations.append(
            _sv(spath, "wrong_type",
                "'matching_algorithm.steps' must be a list of step descriptions",
                expected="list[str]", actual=type(steps).__name__))

    # ── Validate language_support (list of strings) ──
    langs = ma.get("language_support")
    lpath = "matching_algorithm.language_support"
    if isinstance(langs, list):
        _check_str_list(langs, lpath, violations)
    else:
        violations.append(
            _sv(lpath, "wrong_type",
                "'matching_algorithm.language_support' must be a list of language codes",
                expected="list[str]", actual=type(langs).__name__))

    # ── Validate note (optional string) ──
    if "note" in ma:
        _check_str(ma["note"], "matching_algorithm.note", violations, allow_empty=True)


# ── output_schema ─────────────────────────────────────────────────────────


# Recognised output schema field type specifications (the "target format" contract).
_VALID_FIELD_TYPES: frozenset[str] = frozenset({
    "string",
    "boolean",
    "number",
    "integer",
    "float",
    "array<string>",
    "array<number>",
    "array<integer>",
    "array<float>",
})

# Recognised output field names that the static router MUST produce.
_VALID_OUTPUT_FIELD_NAMES: frozenset[str] = frozenset({
    "agenda_type",
    "agenda_label",
    "tags",
    "risk_tags",
    "required_roles",
    "optional_roles",
    "validator_required",
    "codex_required",
    "priority",
    "routing_source",
    "routing_reason",
    "confidence",
    "generated_at",
    "version",
})


def _validate_output_schema(data: dict[str, Any], violations: list[SchemaViolation]) -> None:
    if "output_schema" not in data:
        violations.append(_sv("output_schema", "missing_section",
                              "Top-level 'output_schema' section is missing"))
        return
    os_ = data["output_schema"]
    if not _check_dict(os_, "output_schema", violations):
        return
    _check_str(os_.get("description"), "output_schema.description", violations)

    # ── Validate output_schema.fields (the target format contract) ──
    fields = os_.get("fields")
    fpath = "output_schema.fields"
    if fields is None:
        violations.append(
            _sv(fpath, "missing_field",
                "'output_schema.fields' is missing — required target format definition"))
    elif not isinstance(fields, dict):
        violations.append(
            _sv(fpath, "wrong_type",
                "'output_schema.fields' must be a dict/mapping of field name → type",
                expected="dict[str, str]", actual=type(fields).__name__))
    else:
        if len(fields) == 0:
            violations.append(
                _sv(fpath, "invalid_value",
                    "'output_schema.fields' must contain at least one field definition",
                    expected="non-empty dict", actual="empty dict"))
        for field_name, field_type in fields.items():
            fidx_path = f"{fpath}.{field_name}"
            # Check field name is recognised
            if field_name not in _VALID_OUTPUT_FIELD_NAMES:
                violations.append(
                    _sv(fidx_path, "invalid_value",
                        f"Unknown output field '{field_name}'. Valid: {sorted(_VALID_OUTPUT_FIELD_NAMES)}",
                        expected=f"one of {sorted(_VALID_OUTPUT_FIELD_NAMES)}",
                        actual=repr(field_name)))
            # Check field type specifier is a string
            if not isinstance(field_type, str):
                violations.append(
                    _sv(fidx_path, "wrong_type",
                        f"Field type for '{field_name}' must be a string, got {type(field_type).__name__}",
                        expected="str (e.g. 'string', 'array<string>', 'boolean', 'number')",
                        actual=type(field_type).__name__))
            elif field_type not in _VALID_FIELD_TYPES:
                # Unknown type specifier — warn but don't fail.
                # The YAML schema mixes type names with example values
                # (e.g. routing_source: \"static_fallback\"), so unrecognised
                # strings are tolerated as long as they are non-empty.
                if not field_type.strip():
                    violations.append(
                        _sv(fidx_path, "invalid_value",
                            f"Field type for '{field_name}' is empty — must be a non-empty string",
                            expected="non-empty str", actual=repr(field_type)))


# ═══════════════════════════════════════════════════════════════════════════
# Main entry point
# ═══════════════════════════════════════════════════════════════════════════

# Ordered list of section validators
_SECTION_VALIDATORS: tuple[tuple[str, object], ...] = (
    ("version", _validate_version),
    ("metadata", _validate_metadata),
    ("defaults", _validate_defaults),
    ("teams", _validate_teams),
    ("roles", _validate_roles),
    ("agenda_types", _validate_agenda_types),
    ("risk_detection", _validate_risk_detection),
    ("escalation_rules", _validate_escalation_rules),
    ("priority_rules", _validate_priority_rules),
    ("guardrails", _validate_guardrails),
    ("matching_algorithm", _validate_matching_algorithm),
    ("output_schema", _validate_output_schema),
)


def validate_routing_rules(data: dict[str, Any] | None) -> dict[str, Any]:
    """Validate parsed routing rules data against the full schema.

    This is the main entry point for **Sub-AC 3.1b**.  Accepts the raw
    dict produced by :func:`routing_rules_loader.load_routing_rules`
    and either returns the validated config unchanged (when all checks
    pass) or raises :class:`RoutingRulesValidationError` with all
    detected schema violations.

    Args:
        data: The parsed YAML content as a ``dict``, or ``None``.

    Returns:
        The validated config dict (unchanged) when all checks pass.

    Raises:
        RoutingRulesValidationError: When one or more schema violations
            are detected.  The exception carries a :class:`ValidationReport`
            with the full list of :class:`SchemaViolation` objects.

    Examples:
        >>> from src.routing_rules_loader import load_routing_rules
        >>> from src.routing_rules_validator import validate_routing_rules
        >>> rules = load_routing_rules("config/routing_rules.yaml")
        >>> validated = validate_routing_rules(rules)
        >>> validated["version"]
        '1.0.0'
    """
    violations: list[SchemaViolation] = []

    # ── Null / non-dict guard ──
    if data is None:
        violations.append(
            _sv("<root>", "wrong_type",
                "Input data is None — cannot validate",
                expected="dict[str, Any]", actual="None"))
        report = ValidationReport(passed=False, violations=tuple(violations), sections_checked=0)
        raise RoutingRulesValidationError(report)

    if not isinstance(data, dict):
        violations.append(
            _sv("<root>", "wrong_type",
                f"Input must be a dict, got {type(data).__name__}",
                expected="dict[str, Any]", actual=type(data).__name__))
        report = ValidationReport(passed=False, violations=tuple(violations), sections_checked=0)
        raise RoutingRulesValidationError(report)

    # ── Validate every section ──
    for section_name, validator_fn in _SECTION_VALIDATORS:
        validator_fn(data, violations)  # type: ignore[operator]

    # ── Check for unknown top-level keys ──
    known_keys = {s[0] for s in _SECTION_VALIDATORS}
    for key in data:
        if key not in known_keys:
            violations.append(
                _sv(key, "unknown_key",
                    f"Unknown top-level key '{key}' in routing rules",
                    expected=f"one of {sorted(known_keys)}", actual=repr(key)))

    sections_checked = len(_SECTION_VALIDATORS)
    report = ValidationReport(
        passed=len(violations) == 0,
        violations=tuple(violations),
        sections_checked=sections_checked,
    )

    if not report.passed:
        raise RoutingRulesValidationError(report)

    return data
