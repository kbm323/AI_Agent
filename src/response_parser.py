"""
Response Parser for Qwen CLI Output — Sub-AC 2c-3
==================================================

Takes the raw stdout string from a Qwen LLM call (via opencode-go CLI) and
produces a structured ``ClassificationResult`` enriched with:

* **teams** — derived from required/optional roles by mapping every ``role_id``
  back to its parent team.
* **priority** — P0 / P1 / P2 / P3 derived from risk_tag severity and
  confidence level.
* **validation_score** / **validation_verdict** — summary of JSON extraction
  and schema validation quality.

Pipeline::

    raw_output → extract_json() → validate_qwen_response()
              → enrich (teams, priority) → ClassificationResult

Every failure path — empty response, no JSON found, malformed JSON, schema
validation error — returns a *valid* ``ClassificationResult`` with
``validation_verdict`` set to ``"fail"`` or ``"escalate"`` so the
Coordinator never has to handle a bare exception from this module.

Independently testable with static sample raw output strings — no LLM
calls required.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from src.qwen_json_extractor import extract_json, QwenExtractionResult
from src.qwen_field_validator import (
    ValidationReport,
    validate_qwen_response,
)
from src.qwen_router import TEAM_ROLES


# ── Classification Result ────────────────────────────────────────────────


@dataclass(frozen=True)
class ClassificationResult:
    """Structured classification produced by the Response Parser.

    This is the final output of Sub-AC 2c-3 — the contract between the
    Qwen router pipeline and the Coordinator's meeting orchestration.
    """

    agenda_type: str
    """Classified meeting type (e.g. ``creative_production``)."""

    tags: tuple[str, ...]
    """Topic tags for routing and knowledge retrieval."""

    risk_tags: tuple[str, ...]
    """Risk-indicating tags that trigger validation/escalation rules."""

    required_roles: tuple[str, ...]
    """Role IDs that MUST participate for valid quorum."""

    optional_roles: tuple[str, ...]
    """Role IDs that MAY participate if capacity allows."""

    teams: tuple[str, ...]
    """Team names derived from required + optional roles (deduplicated)."""

    priority: str
    """Derived priority: ``P0`` / ``P1`` / ``P2`` / ``P3``."""

    confidence: float
    """Router confidence 0.0–1.0 (from Qwen response or default)."""

    reasoning: str
    """Brief explanation of classification rationale."""

    validation_score: float
    """0.0–1.0 score reflecting extraction + schema validation quality."""

    validation_verdict: str
    """One of: ``pass``, ``conditional_pass``, ``revision_required``,
    ``escalate``, ``fail``."""

    validator_required: bool
    """Whether GLM-5.1 validation is needed for this meeting."""

    codex_required: bool
    """Whether Codex GPT-5.5 dual-validation is needed."""

    exit_condition: str = ""
    """Exit condition when classification cannot proceed normally.
    Seed-defined values: ``""`` (normal), ``"rate_limit_paused"``
    (quota exhausted — resumable from manifest after quota reset),
    ``"resource_exhausted"`` (system resources depleted)."""

    @property
    def is_valid(self) -> bool:
        """True when the classification is usable (verdict != fail)."""
        return self.validation_verdict != "fail"

    @property
    def is_rate_limited(self) -> bool:
        """True when classification was paused due to rate limits."""
        return self.exit_condition == "rate_limit_paused"

    @property
    def needs_escalation(self) -> bool:
        """True when human review or Codex intervention is required."""
        return self.validation_verdict in ("escalate", "fail")


# ── Priority derivation ──────────────────────────────────────────────────

# Priority thresholds map risk_tag severity to priority levels.
# P0: critical risks that could cause severe harm
# P1: high-impact risks with financial/reputation implications
# P2: moderate risks with operational impact
# P3: low/no risk
_P0_RISK_TAGS: frozenset[str] = frozenset({
    "security", "data_loss",
})
_P1_RISK_TAGS: frozenset[str] = frozenset({
    "legal", "budget", "brand", "external",
})
_P2_RISK_TAGS: frozenset[str] = frozenset({
    "technical", "schedule",
})


def _derive_priority(risk_tags: tuple[str, ...], confidence: float) -> str:
    """Derive P0–P3 priority from risk tags and confidence.

    Rules (first match wins):
    1. Any P0-level risk tag → P0
    2. Two or more P1-level risk tags → P0 (cumulative escalation)
    3. Any P1-level risk tag → P1
    4. Any P2-level risk tag → P2
    5. Fallback → P3
    """
    has_p0 = any(t in _P0_RISK_TAGS for t in risk_tags)
    if has_p0:
        return "P0"

    p1_count = sum(1 for t in risk_tags if t in _P1_RISK_TAGS)
    if p1_count >= 2:
        return "P0"
    if p1_count == 1:
        return "P1"

    has_p2 = any(t in _P2_RISK_TAGS for t in risk_tags)
    if has_p2:
        return "P2"

    return "P3"


# ── Team mapping ─────────────────────────────────────────────────────────

# Build a lookup: role_id → team_name (kebab-case)
_ROLE_TO_TEAM: dict[str, str] = {}
for _team_name, _roles in TEAM_ROLES.items():
    for _role in _roles:
        _ROLE_TO_TEAM[_role["role_id"]] = _team_name


def _roles_to_teams(role_ids: tuple[str, ...]) -> tuple[str, ...]:
    """Map a tuple of role_ids to their parent team names (deduplicated).

    Preserves insertion order (first occurrence of each team wins).
    Unknown role_ids are silently skipped.
    """
    seen: set[str] = set()
    teams: list[str] = []
    for rid in role_ids:
        team = _ROLE_TO_TEAM.get(rid)
        if team and team not in seen:
            seen.add(team)
            teams.append(team)
    return tuple(teams)


# ── Validation scoring ──────────────────────────────────────────────────


def _compute_validation_score(
    extraction: QwenExtractionResult,
    report: ValidationReport | None,
) -> float:
    """Compute a 0.0–1.0 score from extraction + validation quality.

    Scoring breakdown:
    - Base 1.0
    - Extraction failure: 0.0
    - Extracted but repaired: -0.15
    - Validation errors: -0.10 per error (floor 0.0)
    """
    if not extraction.success:
        return 0.0

    score = 1.0

    if extraction.was_repaired:
        score -= 0.15

    if report is not None and not report.passed:
        score -= min(0.10 * report.error_count, 0.70)

    return max(0.0, round(score, 2))


def _compute_verdict(
    extraction: QwenExtractionResult,
    report: ValidationReport | None,
    score: float,
) -> str:
    """Determine validation_verdict from extraction + validation state."""
    if not extraction.success:
        return "fail"

    if report is None:
        # Should not happen if extraction succeeded, but guard
        return "escalate"

    if report.passed and not extraction.was_repaired:
        return "pass"

    if report.passed and extraction.was_repaired:
        return "conditional_pass"

    if score >= 0.70:
        return "conditional_pass"

    if score >= 0.40:
        return "revision_required"

    return "escalate"


# ── Public API ───────────────────────────────────────────────────────────


# Default Qwen model response fields used as fallback when parsing succeeds
# but some fields are absent from the Qwen output.
_DEFAULT_RESULT: dict = {
    "agenda_type": "general_planning",
    "confidence": 0.5,
    "reasoning": "",
    "validator_required": False,
    "codex_required": False,
}


def parse_response(raw_text: str) -> ClassificationResult:
    """Parse raw Qwen CLI stdout into a structured ``ClassificationResult``.

    This is the **single entry point** for Sub-AC 2c-3.  It accepts the
    raw string output from an opencode-go Qwen call and runs the full
    extraction → validation → enrichment pipeline.

    **Handled cases:**

    * Clean JSON: ``{"agenda_type": "creative_production", ...}``
    * Markdown-fenced: ```` ```json\\n{...}\\n``` ````
    * Leading/trailing text
    * Truncated / malformed JSON (repair attempted)
    * Empty response
    * Non-JSON response
    * Schema validation failures (missing fields, wrong types)

    Args:
        raw_text: Raw stdout string from a Qwen LLM call.

    Returns:
        ``ClassificationResult`` — always returns a valid object, never
        raises.  Check ``result.is_valid`` or ``result.validation_verdict``
        to determine if the classification is usable.

    Examples:
        >>> result = parse_response('{"agenda_type": "creative_production", '
        ...     '"tags": ["art"], "risk_tags": [], '
        ...     '"required_roles": ["coordinator"], '
        ...     '"optional_roles": [], '
        ...     '"validator_required": false, '
        ...     '"codex_required": false, '
        ...     '"confidence": 0.9, '
        ...     '"reasoning": "Simple art task."}')
        >>> result.agenda_type
        'creative_production'
        >>> result.priority
        'P3'
        >>> result.validation_verdict
        'pass'

        >>> result = parse_response("")
        >>> result.validation_verdict
        'fail'
        >>> result.is_valid
        False
    """
    # ── Stage 1: Extract JSON ──────────────────────────────────────
    extraction = extract_json(raw_text)

    if not extraction.success:
        return ClassificationResult(
            agenda_type=_DEFAULT_RESULT["agenda_type"],
            tags=(),
            risk_tags=(),
            required_roles=(),
            optional_roles=(),
            teams=(),
            priority="P2",  # uncertain → conservative default
            confidence=0.0,
            reasoning=(
                extraction.error.message if extraction.error
                else "Unknown extraction failure"
            ),
            validation_score=0.0,
            validation_verdict="fail",
            validator_required=True,  # fail-safe: validate
            codex_required=False,
            exit_condition=extraction.exit_condition or "",
        )

    # ── Stage 2: Validate schema ───────────────────────────────────
    data = extraction.data or {}
    report = validate_qwen_response(data)
    score = _compute_validation_score(extraction, report)
    verdict = _compute_verdict(extraction, report, score)

    # ── Stage 3: Extract fields ────────────────────────────────────
    agenda_type = str(data.get("agenda_type", _DEFAULT_RESULT["agenda_type"])).strip()
    tags = _safe_tuple_of_strings(data.get("tags", []))
    risk_tags = _safe_tuple_of_strings(data.get("risk_tags", []))
    required_roles = _safe_tuple_of_strings(data.get("required_roles", []))
    optional_roles = _safe_tuple_of_strings(data.get("optional_roles", []))
    validator_required = bool(data.get("validator_required", _DEFAULT_RESULT["validator_required"]))
    codex_required = bool(data.get("codex_required", _DEFAULT_RESULT["codex_required"]))

    raw_confidence = data.get("confidence")
    if isinstance(raw_confidence, (int, float)) and not isinstance(raw_confidence, bool):
        confidence = min(max(float(raw_confidence), 0.0), 1.0)
    else:
        confidence = float(_DEFAULT_RESULT["confidence"])

    reasoning = str(data.get("reasoning", _DEFAULT_RESULT["reasoning"])).strip()

    # ── Stage 4: Enrich ────────────────────────────────────────────
    teams = _roles_to_teams(required_roles + optional_roles)
    priority = _derive_priority(risk_tags, confidence)

    return ClassificationResult(
        agenda_type=agenda_type,
        tags=tags,
        risk_tags=risk_tags,
        required_roles=required_roles,
        optional_roles=optional_roles,
        teams=teams,
        priority=priority,
        confidence=confidence,
        reasoning=reasoning,
        validation_score=score,
        validation_verdict=verdict,
        validator_required=validator_required,
        codex_required=codex_required,
    )


# ── Internal helpers ─────────────────────────────────────────────────────


def _safe_tuple_of_strings(value: object) -> tuple[str, ...]:
    """Safely convert any value to a tuple of non-empty stripped lowercase strings."""
    if not isinstance(value, list):
        return ()
    result: list[str] = []
    for item in value:
        if isinstance(item, str):
            s = item.strip().lower()
            if s:
                result.append(s)
    return tuple(result)
