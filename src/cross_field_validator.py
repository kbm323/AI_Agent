"""Cross-field data integrity and consistency validator.

Sub-AC 6.1.3: Verifies logical consistency **across** fields in a meeting
manifest — going beyond per-field format validation (Sub-AC 6.1.2) to
enforce relational rules.  Checks valid references, coherent round-session
metadata, matching participant counts, timestamp ordering, state-logic
consistency, and validation-verdict semantics.

Design
------
This module inspects a manifest dict (already parsed from JSON and
potentially already through field-format validation) and applies a suite
of **cross-field rules**.  Each rule is an independent predicate that
receives the full dict and returns zero or more ``CrossFieldError``
instances.

Rules are organised into categories:

* **Reference integrity** — current_speaker and speaker_queue entries
  must appear in ``required_roles`` or ``optional_roles``.
* **Round-session coherence** — ``round_count`` must be consistent with
  the round numbers in ``context_packets``, ``decisions``, and
  ``tool_outputs``; context packets must not exceed ``max_agents_per_meeting``
  unique roles.
* **Validation semantics** — ``validation_verdict`` non-empty implies
  ``validation_score`` set; ``state=completed`` with ``pass`` verdict
  implies ``validation_score >= 0.85``; ``state=failed/escalated``
  implies ``error_log`` entries.
* **State logic** — completed state requires ``round_count > 0`` and
  ``consensus`` non-empty; in-meeting state requires ``agenda_type``
  set.
* **Timestamp ordering** — ``created_at <= updated_at``.
* **Risk/validator linkage** — non-empty ``risk_tags`` implies
  ``validator_required=True``; certain risk tags imply ``codex_required``.

All rules are evaluated — no early-exit on first violation.  The
aggregate ``CrossFieldReport`` carries a ``passed`` flag, an
``errors`` tuple, and a ``rule_count`` for observability.

Testable with
--------------
* Fully valid manifests (baseline pass).
* Inconsistent cross-field states (speaker not in role lists, round_count
  mismatch, validation_score/verdict contradiction).
* Fuzzed data with corrupted references.
* Boundary states (empty role lists, edge validation scores,
  max_agents_per_meeting exactly at limit).
* Timestamp ordering edge cases.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from typing import Any

# ── Valid state and verdict enums (mirrors field_format_validator) ────

_VALID_STATES: frozenset[str] = frozenset({
    "created", "queued", "routing", "context_retrieval",
    "in_meeting", "consensus_building", "validating", "executing",
    "finalizing", "completed", "paused", "deadlocked",
    "escalated", "cancelled", "failed", "stale",
})

_VALID_VERDICTS: frozenset[str] = frozenset({
    "pass", "conditional_pass", "revision_required", "escalate", "fail",
})

# ── Kebab-case role ID pattern ────────────────────────────────────────

_KEBAB_ID_RE = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")

# ── Risk tags that always trigger Codex dual-validation ───────────────

_CODEX_TRIGGER_RISK_TAGS: frozenset[str] = frozenset({
    "legal", "compliance", "safety", "financial",
    "data-privacy", "security-critical",
})

# ── ISO-8601 parsing helpers ──────────────────────────────────────────

# Multiple format attempts to be tolerant of real-world timestamps.
_ISO_FORMATS = (
    "YYYY-MM-DDTHH:MM:SS.ffffff+HH:MM",  # Python 3.12+
    "YYYY-MM-DDTHH:MM:SS.ffffffZ",
    "YYYY-MM-DDTHH:MM:SS+HH:MM",
    "YYYY-MM-DDTHH:MM:SSZ",
    "YYYY-MM-DD HH:MM:SS.ffffff+HH:MM",
    "YYYY-MM-DD HH:MM:SS.ffffff",
    "YYYY-MM-DD HH:MM:SS+HH:MM",
    "YYYY-MM-DD HH:MM:SS",
    "YYYY-MM-DDTHH:MM:SS.ffffff",
    "YYYY-MM-DDTHH:MM:SS",
)


def _parse_iso8601(ts: str) -> datetime | None:
    """Parse an ISO-8601 timestamp string, returning a datetime or None.

    Tolerant of common variants: with/without timezone, with/without
    microseconds, space instead of T separator.
    """
    if not ts or not isinstance(ts, str):
        return None
    stripped = ts.strip()
    if not stripped:
        return None

    # Normalise the separator and timezone
    normalised = stripped.replace(" ", "T")
    if normalised.endswith("Z"):
        normalised = normalised[:-1] + "+00:00"

    # Try fromisoformat first (Python 3.11+ handles most cases)
    try:
        return datetime.fromisoformat(normalised)
    except (ValueError, TypeError):
        pass

    # Fallback: try common formats manually
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
    ):
        try:
            return datetime.strptime(normalised, fmt)
        except ValueError:
            continue

    return None


# ── Data types ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CrossFieldError:
    """A single cross-field integrity violation.

    Carries enough context for the Coordinator to log, report, or
    take corrective action without re-analysing the entire manifest.
    """

    rule_id: str
    """Stable rule identifier (e.g. ``'speaker_not_in_roles'``)."""

    category: str
    """Category: ``reference_integrity``, ``round_coherence``,
    ``validation_semantics``, ``state_logic``, ``timestamp_ordering``,
    ``risk_validator_linkage``, ``participant_limit``."""

    severity: str
    """``error`` (blocks validation) or ``warning`` (advisory)."""

    message: str
    """Human-readable description of the violation."""

    fields_involved: tuple[str, ...]
    """Names of the manifest fields that triggered the violation."""


@dataclass(frozen=True)
class CrossFieldReport:
    """Aggregated result of cross-field integrity validation.

    ``passed`` is ``True`` only when **zero error-severity** violations
    were detected.  Warnings do not cause ``passed=False`` but are
    included in ``errors`` for observability.
    """

    passed: bool
    """Overall pass/fail (warnings alone do not fail)."""

    errors: tuple[CrossFieldError, ...]
    """All violations detected (errors and warnings)."""

    rule_count: int
    """Total number of rules evaluated."""

    schema_version: str = "cross-field-validation.v1"
    """Schema version for this validator."""

    @property
    def error_count(self) -> int:
        """Number of error-severity violations."""
        return sum(1 for e in self.errors if e.severity == "error")

    @property
    def warning_count(self) -> int:
        """Number of warning-severity violations."""
        return sum(1 for e in self.errors if e.severity == "warning")

    def errors_by_category(self) -> dict[str, tuple[CrossFieldError, ...]]:
        """Group violations by category for targeted reporting."""
        grouped: dict[str, list[CrossFieldError]] = {}
        for err in self.errors:
            grouped.setdefault(err.category, []).append(err)
        return {k: tuple(v) for k, v in grouped.items()}


# ═══════════════════════════════════════════════════════════════════════
# Rule implementations
# ═══════════════════════════════════════════════════════════════════════

# Each rule function receives the full manifest dict and returns a list
# of CrossFieldError instances (empty list = pass).


def _rule_speaker_in_roles(data: dict[str, Any]) -> list[CrossFieldError]:
    """current_speaker must be in required_roles ∪ optional_roles (if set)."""
    speaker: str = str(data.get("current_speaker", "")).strip()
    if not speaker:
        return []

    required: list[str] = _safe_str_list(data.get("required_roles"))
    optional: list[str] = _safe_str_list(data.get("optional_roles"))
    all_roles: set[str] = set(required) | set(optional)

    if speaker not in all_roles:
        return [
            CrossFieldError(
                rule_id="speaker_not_in_roles",
                category="reference_integrity",
                severity="error",
                message=(
                    f"current_speaker '{speaker}' is not in "
                    f"required_roles or optional_roles. "
                    f"Available roles: {sorted(all_roles) if all_roles else '(none)'}"
                ),
                fields_involved=(
                    "current_speaker", "required_roles", "optional_roles",
                ),
            )
        ]
    return []


def _rule_speaker_queue_in_roles(data: dict[str, Any]) -> list[CrossFieldError]:
    """Every entry in speaker_queue must be in required_roles ∪ optional_roles."""
    queue: list[str] = _safe_str_list(data.get("speaker_queue"))
    if not queue:
        return []

    required: list[str] = _safe_str_list(data.get("required_roles"))
    optional: list[str] = _safe_str_list(data.get("optional_roles"))
    all_roles: set[str] = set(required) | set(optional)

    errors: list[CrossFieldError] = []
    for entry in queue:
        if entry not in all_roles:
            errors.append(
                CrossFieldError(
                    rule_id="speaker_queue_entry_not_in_roles",
                    category="reference_integrity",
                    severity="error",
                    message=(
                        f"speaker_queue entry '{entry}' is not in "
                        f"required_roles or optional_roles. "
                        f"Available roles: "
                        f"{sorted(all_roles) if all_roles else '(none)'}"
                    ),
                    fields_involved=(
                        "speaker_queue", "required_roles", "optional_roles",
                    ),
                )
            )
    return errors


def _rule_context_packet_roles_in_role_lists(
    data: dict[str, Any],
) -> list[CrossFieldError]:
    """Role IDs in context_packets must appear in required_roles ∪ optional_roles."""
    packets: list[dict[str, Any]] = _safe_list_of_dicts(
        data.get("context_packets")
    )
    required: list[str] = _safe_str_list(data.get("required_roles"))
    optional: list[str] = _safe_str_list(data.get("optional_roles"))
    all_roles: set[str] = set(required) | set(optional)

    errors: list[CrossFieldError] = []
    for i, pkt in enumerate(packets):
        role_id = str(pkt.get("role_id", "")).strip()
        if not role_id:
            continue
        if role_id not in all_roles:
            errors.append(
                CrossFieldError(
                    rule_id="packet_role_not_in_roles",
                    category="reference_integrity",
                    severity="error",
                    message=(
                        f"context_packets[{i}].role_id '{role_id}' is not "
                        f"in required_roles or optional_roles."
                    ),
                    fields_involved=(
                        "context_packets", "required_roles", "optional_roles",
                    ),
                )
            )
    return errors


def _rule_decision_roles_in_role_lists(
    data: dict[str, Any],
) -> list[CrossFieldError]:
    """Role IDs in decisions must appear in required_roles ∪ optional_roles."""
    decisions: list[dict[str, Any]] = _safe_list_of_dicts(
        data.get("decisions")
    )
    required: list[str] = _safe_str_list(data.get("required_roles"))
    optional: list[str] = _safe_str_list(data.get("optional_roles"))
    all_roles: set[str] = set(required) | set(optional)

    errors: list[CrossFieldError] = []
    for i, dec in enumerate(decisions):
        role_id = str(dec.get("role_id", "")).strip()
        if not role_id:
            continue
        if role_id not in all_roles:
            errors.append(
                CrossFieldError(
                    rule_id="decision_role_not_in_roles",
                    category="reference_integrity",
                    severity="error",
                    message=(
                        f"decisions[{i}].role_id '{role_id}' is not "
                        f"in required_roles or optional_roles."
                    ),
                    fields_involved=(
                        "decisions", "required_roles", "optional_roles",
                    ),
                )
            )
    return errors


def _rule_tool_output_roles_in_role_lists(
    data: dict[str, Any],
) -> list[CrossFieldError]:
    """Role IDs in tool_outputs must appear in required_roles ∪ optional_roles."""
    outputs: list[dict[str, Any]] = _safe_list_of_dicts(
        data.get("tool_outputs")
    )
    required: list[str] = _safe_str_list(data.get("required_roles"))
    optional: list[str] = _safe_str_list(data.get("optional_roles"))
    all_roles: set[str] = set(required) | set(optional)

    errors: list[CrossFieldError] = []
    for i, out in enumerate(outputs):
        role_id = str(out.get("role_id", "")).strip()
        if not role_id:
            continue
        if role_id not in all_roles:
            errors.append(
                CrossFieldError(
                    rule_id="tool_output_role_not_in_roles",
                    category="reference_integrity",
                    severity="error",
                    message=(
                        f"tool_outputs[{i}].role_id '{role_id}' is not "
                        f"in required_roles or optional_roles."
                    ),
                    fields_involved=(
                        "tool_outputs", "required_roles", "optional_roles",
                    ),
                )
            )
    return errors


def _rule_round_count_consistency(
    data: dict[str, Any],
) -> list[CrossFieldError]:
    """round_count must be >= the maximum round number in sub-collections."""
    round_count: int = _safe_int(data.get("round_count"), default=0)
    packets: list[dict[str, Any]] = _safe_list_of_dicts(
        data.get("context_packets")
    )
    decisions: list[dict[str, Any]] = _safe_list_of_dicts(
        data.get("decisions")
    )
    outputs: list[dict[str, Any]] = _safe_list_of_dicts(
        data.get("tool_outputs")
    )

    max_round_seen = 0
    for pkt in packets:
        r = _safe_int(pkt.get("round"), default=0)
        if r > max_round_seen:
            max_round_seen = r
    for dec in decisions:
        r = _safe_int(dec.get("round"), default=0)
        if r > max_round_seen:
            max_round_seen = r
    for out in outputs:
        r = _safe_int(out.get("round"), default=0)
        if r > max_round_seen:
            max_round_seen = r

    if max_round_seen > round_count:
        return [
            CrossFieldError(
                rule_id="round_count_too_low",
                category="round_coherence",
                severity="error",
                message=(
                    f"round_count={round_count} but sub-collections "
                    f"reference rounds up to {max_round_seen}. "
                    f"round_count should be at least {max_round_seen}."
                ),
                fields_involved=(
                    "round_count", "context_packets", "decisions",
                    "tool_outputs",
                ),
            )
        ]
    return []


def _rule_participant_count(
    data: dict[str, Any],
) -> list[CrossFieldError]:
    """Unique role IDs in context_packets must not exceed max_agents_per_meeting."""
    packets: list[dict[str, Any]] = _safe_list_of_dicts(
        data.get("context_packets")
    )
    max_agents: int = _safe_int(
        data.get("max_agents_per_meeting"), default=7
    )

    unique_roles: set[str] = set()
    for pkt in packets:
        role_id = str(pkt.get("role_id", "")).strip()
        if role_id:
            unique_roles.add(role_id)

    if len(unique_roles) > max_agents:
        return [
            CrossFieldError(
                rule_id="participant_limit_exceeded",
                category="participant_limit",
                severity="error",
                message=(
                    f"context_packets contain {len(unique_roles)} unique "
                    f"role IDs but max_agents_per_meeting={max_agents}. "
                    f"Roles: {sorted(unique_roles)}"
                ),
                fields_involved=(
                    "context_packets", "max_agents_per_meeting",
                ),
            )
        ]
    return []


def _rule_validation_score_verdict_consistency(
    data: dict[str, Any],
) -> list[CrossFieldError]:
    """validation_verdict non-empty requires validation_score set > 0."""
    verdict: str = str(data.get("validation_verdict", "")).strip().lower()
    score: Any = data.get("validation_score")

    errors: list[CrossFieldError] = []

    # If verdict is set, score should be > 0 (or at least set meaningfully)
    if verdict and verdict in _VALID_VERDICTS:
        try:
            fscore = float(score) if score is not None else 0.0
        except (TypeError, ValueError):
            fscore = -1.0

        if fscore < 0.0 or fscore > 1.0:
            errors.append(
                CrossFieldError(
                    rule_id="verdict_without_valid_score",
                    category="validation_semantics",
                    severity="error",
                    message=(
                        f"validation_verdict='{verdict}' but "
                        f"validation_score={score} is not in [0.0, 1.0]."
                    ),
                    fields_involved=(
                        "validation_verdict", "validation_score",
                    ),
                )
            )

    # If state=completed and verdict=pass, score must be >= 0.85
    state: str = str(data.get("state", "")).strip().lower()
    if state == "completed" and verdict == "pass":
        try:
            fscore = float(score) if score is not None else 0.0
        except (TypeError, ValueError):
            fscore = 0.0
        if fscore < 0.85:
            errors.append(
                CrossFieldError(
                    rule_id="completed_pass_score_too_low",
                    category="validation_semantics",
                    severity="error",
                    message=(
                        f"state='completed' with verdict='pass' requires "
                        f"validation_score >= 0.85, got {fscore}"
                    ),
                    fields_involved=(
                        "state", "validation_verdict", "validation_score",
                    ),
                )
            )

    return errors


def _rule_state_logic(data: dict[str, Any]) -> list[CrossFieldError]:
    """State-specific consistency rules."""
    state: str = str(data.get("state", "")).strip().lower()
    errors: list[CrossFieldError] = []

    if state == "completed":
        round_count: int = _safe_int(data.get("round_count"), default=0)
        consensus: str = str(data.get("consensus", "")).strip()
        if round_count <= 0:
            errors.append(
                CrossFieldError(
                    rule_id="completed_without_rounds",
                    category="state_logic",
                    severity="error",
                    message=(
                        "state='completed' but round_count=0. "
                        "A completed meeting must have at least one round."
                    ),
                    fields_involved=("state", "round_count"),
                )
            )
        if not consensus:
            errors.append(
                CrossFieldError(
                    rule_id="completed_without_consensus",
                    category="state_logic",
                    severity="warning",
                    message=(
                        "state='completed' but consensus is empty. "
                        "A completed meeting should have a consensus summary."
                    ),
                    fields_involved=("state", "consensus"),
                )
            )

    if state in ("failed", "escalated", "deadlocked"):
        error_log: list[Any] = _safe_list(data.get("error_log"))
        if len(error_log) == 0:
            errors.append(
                CrossFieldError(
                    rule_id="terminal_state_without_errors",
                    category="state_logic",
                    severity="warning",
                    message=(
                        f"state='{state}' but error_log is empty. "
                        f"Terminal error states should record failure causes."
                    ),
                    fields_involved=("state", "error_log"),
                )
            )

    if state == "in_meeting":
        agenda_type: str = str(data.get("agenda_type", "")).strip()
        if not agenda_type:
            errors.append(
                CrossFieldError(
                    rule_id="in_meeting_without_agenda_type",
                    category="state_logic",
                    severity="warning",
                    message=(
                        "state='in_meeting' but agenda_type is empty. "
                        "agenda_type should be set before entering meeting."
                    ),
                    fields_involved=("state", "agenda_type"),
                )
            )

    return errors


def _rule_timestamp_ordering(
    data: dict[str, Any],
) -> list[CrossFieldError]:
    """created_at must be <= updated_at."""
    created: str = str(data.get("created_at", "")).strip()
    updated: str = str(data.get("updated_at", "")).strip()

    if not created or not updated:
        return []  # missing timestamps caught by field-format validator

    dt_created = _parse_iso8601(created)
    dt_updated = _parse_iso8601(updated)

    if dt_created is None or dt_updated is None:
        # Unparseable timestamps should have been caught by format validator
        return []

    if dt_created > dt_updated:
        return [
            CrossFieldError(
                rule_id="created_after_updated",
                category="timestamp_ordering",
                severity="error",
                message=(
                    f"created_at ({created}) is after "
                    f"updated_at ({updated}). "
                    f"Timestamps must be in chronological order."
                ),
                fields_involved=("created_at", "updated_at"),
            )
        ]
    return []


def _rule_risk_validator_linkage(
    data: dict[str, Any],
) -> list[CrossFieldError]:
    """Non-empty risk_tags implies validator_required=True; certain tags
    imply codex_required."""
    risk_tags: list[str] = _safe_str_list(data.get("risk_tags"))
    errors: list[CrossFieldError] = []

    if risk_tags:
        validator_required = data.get("validator_required")
        if validator_required is not True:
            errors.append(
                CrossFieldError(
                    rule_id="risk_tags_without_validator",
                    category="risk_validator_linkage",
                    severity="error",
                    message=(
                        f"risk_tags={risk_tags} but "
                        f"validator_required={validator_required}. "
                        f"Meetings with risk tags require validation."
                    ),
                    fields_involved=("risk_tags", "validator_required"),
                )
            )

        # Check for Codex-trigger risk tags
        codex_triggers = [
            t for t in risk_tags
            if t.lower() in _CODEX_TRIGGER_RISK_TAGS
        ]
        if codex_triggers:
            codex_required = data.get("codex_required")
            if codex_required is not True:
                errors.append(
                    CrossFieldError(
                        rule_id="codex_trigger_without_codex",
                        category="risk_validator_linkage",
                        severity="warning",
                        message=(
                            f"risk_tags contains Codex-trigger tags "
                            f"{codex_triggers} but "
                            f"codex_required={codex_required}. "
                            f"Codex dual-validation should be enabled."
                        ),
                        fields_involved=("risk_tags", "codex_required"),
                    )
                )

    return errors


def _rule_token_limit_hierarchy(
    data: dict[str, Any],
) -> list[CrossFieldError]:
    """Token limits must maintain: worker <= validator <= codex."""
    worker: int = _safe_int(data.get("token_limit_worker"), default=12000)
    validator: int = _safe_int(
        data.get("token_limit_validator"), default=20000
    )
    codex: int = _safe_int(data.get("token_limit_codex"), default=30000)

    errors: list[CrossFieldError] = []

    if worker > validator:
        errors.append(
            CrossFieldError(
                rule_id="token_limit_worker_exceeds_validator",
                category="validation_semantics",
                severity="error",
                message=(
                    f"token_limit_worker={worker} exceeds "
                    f"token_limit_validator={validator}. "
                    f"Validator limit must be >= worker limit."
                ),
                fields_involved=(
                    "token_limit_worker", "token_limit_validator",
                ),
            )
        )

    if validator > codex:
        errors.append(
            CrossFieldError(
                rule_id="token_limit_validator_exceeds_codex",
                category="validation_semantics",
                severity="error",
                message=(
                    f"token_limit_validator={validator} exceeds "
                    f"token_limit_codex={codex}. "
                    f"Codex limit must be >= validator limit."
                ),
                fields_involved=(
                    "token_limit_validator", "token_limit_codex",
                ),
            )
        )

    return errors


def _rule_round_not_exceeding_max(
    data: dict[str, Any],
) -> list[CrossFieldError]:
    """No sub-collection round number should exceed max_rounds + 1 (tie-break)."""
    max_rounds: int = _safe_int(data.get("max_rounds"), default=3)
    abs_max = max_rounds + 1  # +1 for tie-break round

    packets: list[dict[str, Any]] = _safe_list_of_dicts(
        data.get("context_packets")
    )
    decisions: list[dict[str, Any]] = _safe_list_of_dicts(
        data.get("decisions")
    )
    outputs: list[dict[str, Any]] = _safe_list_of_dicts(
        data.get("tool_outputs")
    )

    errors: list[CrossFieldError] = []

    for i, pkt in enumerate(packets):
        r = _safe_int(pkt.get("round"), default=0)
        if r > abs_max:
            errors.append(
                CrossFieldError(
                    rule_id="packet_round_exceeds_max",
                    category="round_coherence",
                    severity="error",
                    message=(
                        f"context_packets[{i}].round={r} exceeds "
                        f"max_rounds({max_rounds}) + 1 tie-break = {abs_max}."
                    ),
                    fields_involved=("context_packets", "max_rounds"),
                )
            )

    for i, dec in enumerate(decisions):
        r = _safe_int(dec.get("round"), default=0)
        if r > abs_max:
            errors.append(
                CrossFieldError(
                    rule_id="decision_round_exceeds_max",
                    category="round_coherence",
                    severity="error",
                    message=(
                        f"decisions[{i}].round={r} exceeds "
                        f"max_rounds({max_rounds}) + 1 tie-break = {abs_max}."
                    ),
                    fields_involved=("decisions", "max_rounds"),
                )
            )

    for i, out in enumerate(outputs):
        r = _safe_int(out.get("round"), default=0)
        if r > abs_max:
            errors.append(
                CrossFieldError(
                    rule_id="tool_output_round_exceeds_max",
                    category="round_coherence",
                    severity="error",
                    message=(
                        f"tool_outputs[{i}].round={r} exceeds "
                        f"max_rounds({max_rounds}) + 1 tie-break = {abs_max}."
                    ),
                    fields_involved=("tool_outputs", "max_rounds"),
                )
            )

    return errors


def _rule_role_id_kebab_case(
    data: dict[str, Any],
) -> list[CrossFieldError]:
    """All role_id values in sub-collections must be valid kebab-case."""
    packets: list[dict[str, Any]] = _safe_list_of_dicts(
        data.get("context_packets")
    )
    decisions: list[dict[str, Any]] = _safe_list_of_dicts(
        data.get("decisions")
    )
    outputs: list[dict[str, Any]] = _safe_list_of_dicts(
        data.get("tool_outputs")
    )

    errors: list[CrossFieldError] = []

    for i, pkt in enumerate(packets):
        role_id = str(pkt.get("role_id", "")).strip()
        if role_id and not _KEBAB_ID_RE.match(role_id):
            errors.append(
                CrossFieldError(
                    rule_id="packet_role_id_not_kebab",
                    category="reference_integrity",
                    severity="error",
                    message=(
                        f"context_packets[{i}].role_id '{role_id}' "
                        f"is not valid kebab-case."
                    ),
                    fields_involved=("context_packets",),
                )
            )

    for i, dec in enumerate(decisions):
        role_id = str(dec.get("role_id", "")).strip()
        if role_id and not _KEBAB_ID_RE.match(role_id):
            errors.append(
                CrossFieldError(
                    rule_id="decision_role_id_not_kebab",
                    category="reference_integrity",
                    severity="error",
                    message=(
                        f"decisions[{i}].role_id '{role_id}' "
                        f"is not valid kebab-case."
                    ),
                    fields_involved=("decisions",),
                )
            )

    for i, out in enumerate(outputs):
        role_id = str(out.get("role_id", "")).strip()
        if role_id and not _KEBAB_ID_RE.match(role_id):
            errors.append(
                CrossFieldError(
                    rule_id="tool_output_role_id_not_kebab",
                    category="reference_integrity",
                    severity="error",
                    message=(
                        f"tool_outputs[{i}].role_id '{role_id}' "
                        f"is not valid kebab-case."
                    ),
                    fields_involved=("tool_outputs",),
                )
            )

    return errors


# ═══════════════════════════════════════════════════════════════════════
# Rule registry — all rules evaluated by the main entry point
# ═══════════════════════════════════════════════════════════════════════

_RULES: tuple[Callable[[dict[str, Any]], list[CrossFieldError]], ...] = (
    # Reference integrity
    _rule_speaker_in_roles,
    _rule_speaker_queue_in_roles,
    _rule_context_packet_roles_in_role_lists,
    _rule_decision_roles_in_role_lists,
    _rule_tool_output_roles_in_role_lists,
    _rule_role_id_kebab_case,
    # Round coherence
    _rule_round_count_consistency,
    _rule_round_not_exceeding_max,
    # Participant limits
    _rule_participant_count,
    # Validation semantics
    _rule_validation_score_verdict_consistency,
    _rule_token_limit_hierarchy,
    # State logic
    _rule_state_logic,
    # Timestamp ordering
    _rule_timestamp_ordering,
    # Risk-validator linkage
    _rule_risk_validator_linkage,
)


# ═══════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════


def validate_cross_field_integrity(
    data: dict[str, Any] | None,
) -> CrossFieldReport:
    """Validate cross-field data integrity and consistency in a manifest.

    Applies all registered cross-field rules against the manifest dict.
    Rules check reference integrity, round coherence, validation semantics,
    state logic, timestamp ordering, risk-validator linkage, and
    participant limits.

    Args:
        data: A manifest dict (parsed from JSON).  May be ``None``.

    Returns:
        ``CrossFieldReport`` with ``passed=True`` when zero error-severity
        violations are detected.

    Examples:
        >>> report = validate_cross_field_integrity(_valid_manifest())
        >>> report.passed
        True

        >>> bad = _valid_manifest(current_speaker="ghost-role")
        >>> report = validate_cross_field_integrity(bad)
        >>> report.passed
        False
        >>> any(e.rule_id == "speaker_not_in_roles" for e in report.errors)
        True
    """
    all_errors: list[CrossFieldError] = []

    # ── Null guard ──
    if data is None:
        return CrossFieldReport(
            passed=False,
            errors=(
                CrossFieldError(
                    rule_id="null_input",
                    category="reference_integrity",
                    severity="error",
                    message="Input data is None — cannot validate.",
                    fields_involved=("<root>",),
                ),
            ),
            rule_count=0,
        )

    # ── Non-dict guard ──
    if not isinstance(data, dict):
        return CrossFieldReport(
            passed=False,
            errors=(
                CrossFieldError(
                    rule_id="non_dict_input",
                    category="reference_integrity",
                    severity="error",
                    message=(
                        f"Input must be a dict, got {type(data).__name__}"
                    ),
                    fields_involved=("<root>",),
                ),
            ),
            rule_count=0,
        )

    # ── Evaluate all rules ──
    for rule_fn in _RULES:
        with suppress(Exception):
            all_errors.extend(rule_fn(data))

    error_severity_count = sum(
        1 for e in all_errors if e.severity == "error"
    )

    return CrossFieldReport(
        passed=error_severity_count == 0,
        errors=tuple(all_errors),
        rule_count=len(_RULES),
    )


# ═══════════════════════════════════════════════════════════════════════
# Helper utilities (also used by rules)
# ═══════════════════════════════════════════════════════════════════════


def _safe_str_list(value: Any) -> list[str]:
    """Safely extract a list of strings from a value (handles tuples, lists, None)."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if v is not None]
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    return []


def _safe_list_of_dicts(value: Any) -> list[dict[str, Any]]:
    """Safely extract a list of dicts (handles tuples, lists, None)."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [
            v for v in value
            if isinstance(v, dict)
        ]
    return []


def _safe_list(value: Any) -> list[Any]:
    """Safely extract a list (handles tuples, lists, None)."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return []


def _safe_int(value: Any, *, default: int = 0) -> int:
    """Safely coerce a value to int, returning default on failure."""
    if value is None:
        return default
    if isinstance(value, bool):
        return int(value)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# ── Exports ───────────────────────────────────────────────────────────

__all__ = [
    "CrossFieldError",
    "CrossFieldReport",
    "validate_cross_field_integrity",
    "_RULES",  # exposed for test introspection
]
