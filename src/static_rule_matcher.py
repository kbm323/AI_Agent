"""Static rule matching engine for fallback routing (Sub-AC 3.2a).

Deterministic keyword-based meeting route classification. Activated when
the Qwen LLM primary router is unavailable (timeout, parse failure,
rate limit, or opencode-go CLI unavailability). Uses the same
``routing_rules.yaml`` configuration as the validator and guardrail
layers.

Algorithm (from ``matching_algorithm`` in routing_rules.yaml):
    1. Normalize input (lowercase, strip punctuation, tokenize)
    2. For each agenda_type (in order), check each keyword group
    3. A keyword group matches if ANY keyword in the group is found in input
    4. An agenda_type matches if ALL its keyword groups match
    5. First matching agenda_type wins (order is significant)
    6. If no agenda_type matches, use 'general-discussion' as catch-all
    7. After classification: run risk_detection patterns
    8. Apply escalation_rules to determine codex_required
    9. Apply guardrails as hard overrides
    10. Return final routing result

Design principles:
- **Deterministic**: same input → identical output, testable without LLM
- **Independently testable**: accepts arbitrary rule sets for isolated testing
- **Output-compatible**: produces identical schema to Qwen LLM output
- **No side effects**: pure function over (context, config) → result
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any


# ═══════════════════════════════════════════════════════════════════════════
# Data models
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class MeetingContext:
    """Immutable meeting context for route matching.

    Attributes:
        topic: Raw meeting agenda text from the user (required).
        meeting_type: Pre-classified meeting type if available from Qwen.
        participants: Role IDs already participating or requested.
        priority: Priority level if known (P0-P3).
    """

    topic: str
    meeting_type: str = ""
    participants: tuple[str, ...] = ()
    priority: str = ""

    def __post_init__(self) -> None:
        # Ensure participants is always a tuple
        if not isinstance(self.participants, tuple):
            object.__setattr__(self, "participants", tuple(self.participants))


@dataclass(frozen=True)
class MatchResult:
    """Immutable routing result produced by the static rule matcher.

    Follows the ``output_schema`` from routing_rules.yaml exactly
    so downstream consumers (Coordinator, meeting loop) do not
    need fallback-specific parsing.
    """

    agenda_type: str
    """Matched agenda type ID (e.g. ``'creative-production'``)."""

    agenda_label: str
    """Human-readable display name (e.g. ``'창작 제작 회의'``)."""

    tags: tuple[str, ...]
    """Topic tags for routing and knowledge retrieval."""

    risk_tags: tuple[str, ...]
    """Risk-indicating tags that trigger validation/escalation rules."""

    required_roles: tuple[str, ...]
    """Role IDs that MUST participate for valid quorum."""

    optional_roles: tuple[str, ...]
    """Role IDs that MAY participate if capacity allows."""

    validator_required: bool
    """Whether GLM-5.1 validation is needed."""

    codex_required: bool
    """Whether Codex GPT-5.5 dual-validation is needed."""

    priority: str
    """Priority level: P0, P1, P2, or P3."""

    routing_source: str
    """Always ``'static_fallback'`` when this engine produces the result."""

    routing_reason: str
    """Why the fallback was activated (e.g. ``'qwen_timeout'``)."""

    confidence: float
    """Static fallback confidence is always 0.7."""

    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        compare=False,
    )
    """ISO 8601 timestamp of generation."""

    version: str = "1.0.0"
    """Schema version."""

    requires_approval: tuple[str, ...] = ()
    """Risk tags that require human-in-the-loop approval."""

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-serializable dict matching the output schema."""
        return {
            "agenda_type": self.agenda_type,
            "agenda_label": self.agenda_label,
            "tags": list(self.tags),
            "risk_tags": list(self.risk_tags),
            "required_roles": list(self.required_roles),
            "optional_roles": list(self.optional_roles),
            "validator_required": self.validator_required,
            "codex_required": self.codex_required,
            "priority": self.priority,
            "routing_source": self.routing_source,
            "routing_reason": self.routing_reason,
            "confidence": self.confidence,
            "generated_at": self.generated_at,
            "version": self.version,
            "requires_approval": list(self.requires_approval),
        }


@dataclass(frozen=True)
class RuleConfig:
    """Parsed and validated static routing rule configuration.

    Extracted from routing_rules.yaml. Contains all sections needed
    by the static rule matching engine.
    """

    version: str
    agenda_types: tuple[dict[str, Any], ...]
    risk_patterns: tuple[dict[str, Any], ...]
    codex_triggers: tuple[dict[str, Any], ...]
    priority_inference: tuple[dict[str, Any], ...]
    default_priority: str = "P2"
    guardrails: tuple[dict[str, Any], ...] = ()
    validator_role_id: str = "validator"
    codex_model: str = "gpt-5.5"
    validator_model: str = "glm-5.1"
    max_roles_per_meeting: int = 7
    max_required_roles: int = 6
    defaults_validator_required: bool = True
    defaults_codex_required: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RuleConfig:
        """Parse a routing rules YAML dict into a RuleConfig.

        Args:
            data: Raw parsed YAML dict from routing_rules_loader.

        Returns:
            A validated RuleConfig instance.
        """
        defaults = data.get("defaults", {})
        return cls(
            version=str(data.get("version", "1.0.0")),
            agenda_types=tuple(data.get("agenda_types", [])),
            risk_patterns=tuple(data.get("risk_detection", {}).get("patterns", [])),
            codex_triggers=tuple(
                data.get("escalation_rules", {}).get("codex_triggers", [])
            ),
            priority_inference=tuple(
                data.get("priority_rules", {}).get("inference", [])
            ),
            default_priority=str(
                data.get("priority_rules", {}).get("default", "P2")
            ),
            guardrails=tuple(data.get("guardrails", [])),
            validator_role_id=str(
                defaults.get("validator_role_id", "validator")
            ),
            codex_model=str(defaults.get("codex_model", "gpt-5.5")),
            validator_model=str(defaults.get("validator_model", "glm-5.1")),
            max_roles_per_meeting=int(defaults.get("max_roles_per_meeting", 7)),
            max_required_roles=int(defaults.get("max_required_roles", 6)),
            defaults_validator_required=bool(
                defaults.get("validator_required", True)
            ),
            defaults_codex_required=bool(
                defaults.get("codex_required", False)
            ),
        )


# ═══════════════════════════════════════════════════════════════════════════
# Static rule matcher engine
# ═══════════════════════════════════════════════════════════════════════════


# Punctuation pattern for normalization (preserves underscores and Korean chars)
_PUNCT_RE = re.compile(r"[^\w\s\uac00-\ud7af\u1100-\u11ff\u3130-\u318f]")


class StaticRuleMatcher:
    """Deterministic keyword-based meeting route classifier.

    Implements the fallback matching algorithm from routing_rules.yaml.
    Pure logic — no network calls, no LLM dependencies, no side effects.

    Usage::

        rules = load_routing_rules("config/routing_rules.yaml")
        config = RuleConfig.from_dict(rules)
        matcher = StaticRuleMatcher(config)

        ctx = MeetingContext(topic="뮤직비디오 제작 회의")
        result = matcher.match(ctx, routing_reason="qwen_timeout")
        print(result.to_dict())
    """

    def __init__(self, config: RuleConfig) -> None:
        self._config = config

    # ── Public API ──────────────────────────────────────────────────────

    def match(
        self,
        context: MeetingContext,
        *,
        routing_reason: str = "static_fallback",
        timestamp: str | None = None,
    ) -> MatchResult:
        """Match a meeting context to a route using static keyword rules.

        This is the main entry point. It runs the full 10-step matching
        algorithm and returns a complete MatchResult.

        Args:
            context: Meeting context with at minimum a non-empty topic.
            routing_reason: Why the static fallback was activated.
            timestamp: ISO 8601 timestamp for generated_at (for test determinism).

        Returns:
            A complete MatchResult with all routing fields populated.

        Raises:
            ValueError: If the topic is empty or whitespace-only.
        """
        topic = context.topic

        if not topic or not topic.strip():
            raise ValueError("Meeting topic must not be empty")

        normalized = self._normalize(topic)

        # Steps 2-5: Match agenda type by keyword groups
        agenda_match = self._match_agenda_type(normalized)
        if agenda_match is None:
            # Should not happen — general-discussion has empty keywords
            # and matches anything.  But defensively create a catch-all.
            agenda_match = {
                "id": "general-discussion",
                "display_name": "General Discussion",
                "tags": ["general", "discussion"],
                "risk_tags": [],
                "required_roles": ["content-director"],
                "optional_roles": [],
                "validator_required": True,
                "codex_required": False,
            }

        # Step 7: Risk detection
        detected_risks = self._detect_risks(normalized)
        approval_risks = self._detect_approval_risks(normalized)

        # Merge risk_tags from agenda type + risk detection
        agenda_risks = set(agenda_match.get("risk_tags", []))
        all_risks = tuple(sorted(agenda_risks | set(detected_risks)))

        # Step 8: Escalation rules → codex_required
        codex_from_agenda = bool(agenda_match.get("codex_required", False))
        codex_from_escalation = self._evaluate_codex_triggers(all_risks)
        codex_required = codex_from_agenda or codex_from_escalation

        # Infer priority from topic or use context priority or default
        if context.priority and context.priority in ("P0", "P1", "P2", "P3"):
            priority = context.priority
        else:
            priority = self._infer_priority(normalized)

        # Build result
        gen_time = timestamp or datetime.now(timezone.utc).isoformat()
        result = MatchResult(
            agenda_type=str(agenda_match.get("id", "general-discussion")),
            agenda_label=str(agenda_match.get("display_name", "General Discussion")),
            tags=tuple(agenda_match.get("tags", [])),
            risk_tags=all_risks,
            required_roles=tuple(agenda_match.get("required_roles", [])),
            optional_roles=tuple(agenda_match.get("optional_roles", [])),
            validator_required=bool(
                agenda_match.get("validator_required", self._config.defaults_validator_required)
            ),
            codex_required=codex_required,
            priority=priority,
            routing_source="static_fallback",
            routing_reason=routing_reason,
            confidence=0.7,
            generated_at=gen_time,
            requires_approval=tuple(sorted(approval_risks)),
        )

        # Step 9: Guardrails
        result = self._apply_guardrails(result, self._config)

        return result

    # ── Step 1: Normalization ───────────────────────────────────────────

    def _normalize(self, text: str) -> str:
        """Normalize input text for keyword matching.

        1. Lowercase
        2. Strip leading/trailing whitespace
        3. Remove punctuation (preserving underscores, Korean, alphanumeric)
        4. Collapse multiple whitespace to single space

        Args:
            text: Raw meeting topic text.

        Returns:
            Normalized text string (may be empty).
        """
        text = text.strip().lower()
        # Remove punctuation but preserve word chars, spaces, underscores,
        # Korean Hangul (U+AC00-D7AF), and Korean Jamo (U+1100-11FF, U+3130-318F)
        text = _PUNCT_RE.sub(" ", text)
        # Collapse multiple spaces
        text = re.sub(r"\s+", " ", text).strip()
        return text

    # ── Steps 2-5: Agenda type matching ─────────────────────────────────

    def _match_agenda_type(self, normalized_text: str) -> dict[str, Any] | None:
        """Find the first agenda_type whose keyword groups all match.

        For each agenda_type in order:
            For each keyword group:
                Group matches if ANY keyword in the group appears in the text.
            Agenda type matches if ALL its keyword groups match.
            First match wins.

        An agenda_type with empty keywords (e.g. 'general-discussion')
        matches anything.

        Args:
            normalized_text: Normalized, lowercase meeting topic.

        Returns:
            The matched agenda_type dict, or None if no match.
        """
        if not normalized_text:
            return None

        # Tokenize for exact word matching
        tokens = set(normalized_text.split())

        for at in self._config.agenda_types:
            groups = at.get("keywords", [])
            if not groups:
                # Empty keyword list → matches anything (catch-all)
                return at

            if self._all_groups_match(groups, normalized_text, tokens):
                return at

        return None

    def _all_groups_match(
        self,
        groups: list[Any],
        normalized_text: str,
        tokens: set[str],
    ) -> bool:
        """Check if ALL keyword groups match the input.

        A group matches if ANY keyword in the group is found in the
        normalized text (exact token match or substring match for
        multi-word/underscored keywords).
        """
        for group in groups:
            if not isinstance(group, list) or not group:
                continue  # empty group → skip (effectively matches)
            if not self._any_keyword_matches(group, normalized_text, tokens):
                return False
        return True

    def _any_keyword_matches(
        self,
        keywords: list[Any],
        normalized_text: str,
        tokens: set[str],
    ) -> bool:
        """Check if ANY keyword in the group appears in the input.

        Matching strategy:
        1. If the keyword contains spaces or underscores, do substring
           match against the full normalized text (handles multi-word
           phrases and compound terms like 'music_video').
        2. Otherwise, do exact token match (prevents partial-word
           matches like 'code' matching 'coder').
        """
        for kw in keywords:
            kw_str = str(kw).strip().lower()
            if not kw_str:
                continue
            # Multi-word or compound keyword → substring match
            if " " in kw_str or "_" in kw_str:
                if kw_str in normalized_text:
                    return True
            else:
                # Single-word keyword → exact token match
                if kw_str in tokens:
                    return True
        return False

    # ── Step 7: Risk detection ──────────────────────────────────────────

    def _detect_risks(self, normalized_text: str) -> list[str]:
        """Scan input for risk keywords and return matched risk tags.

        Each risk pattern's keywords are checked against the input.
        If any keyword matches, the risk_tag is added to the result.
        Results are deduplicated.

        Args:
            normalized_text: Normalized meeting topic.

        Returns:
            List of matched risk_tag strings (deduplicated, order stable).
        """
        if not normalized_text:
            return []

        tokens = set(normalized_text.split())
        found: list[str] = []
        seen: set[str] = set()

        for pattern in self._config.risk_patterns:
            risk_tag = str(pattern.get("risk_tag", ""))
            if not risk_tag or risk_tag in seen:
                continue
            keywords = pattern.get("keywords", [])
            if self._any_keyword_matches(keywords, normalized_text, tokens):
                found.append(risk_tag)
                seen.add(risk_tag)

        return found

    def _detect_approval_risks(self, normalized_text: str) -> list[str]:
        """Detect risk patterns that require human approval.

        Args:
            normalized_text: Normalized meeting topic.

        Returns:
            List of risk_tag strings that require approval.
        """
        if not normalized_text:
            return []

        tokens = set(normalized_text.split())
        found: list[str] = []
        seen: set[str] = set()

        for pattern in self._config.risk_patterns:
            if not pattern.get("requires_approval", False):
                continue
            risk_tag = str(pattern.get("risk_tag", ""))
            if not risk_tag or risk_tag in seen:
                continue
            keywords = pattern.get("keywords", [])
            if self._any_keyword_matches(keywords, normalized_text, tokens):
                found.append(risk_tag)
                seen.add(risk_tag)

        return found

    # ── Step 8: Escalation rules → codex_required ───────────────────────

    def _evaluate_codex_triggers(self, risk_tags: tuple[str, ...] | list[str]) -> bool:
        """Evaluate Codex escalation triggers against detected risk tags.

        Each codex trigger defines a condition (usually ``any_risk_tag_in``)
        and an action (usually ``set_codex_required_true``).  If any
        trigger fires, return True.

        Args:
            risk_tags: All detected risk tags from agenda match + scanning.

        Returns:
            True if Codex validation is required.
        """
        risk_set = set(risk_tags)
        for trigger in self._config.codex_triggers:
            condition = trigger.get("condition", {})
            if not isinstance(condition, dict):
                continue

            # any_risk_tag_in: list of risk tags — if any present, trigger fires
            trigger_tags = condition.get("any_risk_tag_in", [])
            if isinstance(trigger_tags, list):
                if risk_set & set(trigger_tags):
                    return True

            # Explicit codex_required field on trigger
            if condition.get("codex_required") is True:
                return True

        return False

    # ── Priority inference ──────────────────────────────────────────────

    def _infer_priority(self, normalized_text: str) -> str:
        """Infer meeting priority from topic keywords.

        Scans priority inference rules in order.  First rule whose
        keywords match sets the priority.  Falls back to the configured
        default priority (usually P2).

        Args:
            normalized_text: Normalized meeting topic.

        Returns:
            Priority string: P0, P1, P2, or P3.
        """
        if not normalized_text:
            return self._config.default_priority

        tokens = set(normalized_text.split())

        for rule in self._config.priority_inference:
            if not isinstance(rule, dict):
                continue
            keywords = rule.get("keywords", [])
            if self._any_keyword_matches(keywords, normalized_text, tokens):
                prio = str(rule.get("priority", ""))
                if prio in ("P0", "P1", "P2", "P3"):
                    return prio

        return self._config.default_priority

    # ── Step 9: Guardrails ──────────────────────────────────────────────

    def _apply_guardrails(
        self, result: MatchResult, config: RuleConfig
    ) -> MatchResult:
        """Apply hard guardrail constraints to the routing result.

        Guardrails are non-negotiable system invariants. They override
        even the static matching result.

        Current enforced guardrails:
        - Validator role is always in required_roles
        - Total roles (required + optional) ≤ max_roles_per_meeting
        - Required roles ≤ max_required_roles
        - No overlap between required and optional roles

        Args:
            result: The routing result to guard.
            config: The rule configuration with guardrail settings.

        Returns:
            Guarded MatchResult (may be a new instance if modified).
        """
        required = list(result.required_roles)
        optional = list(result.optional_roles)

        # Guardrail: validator always required
        validator_id = config.validator_role_id
        if validator_id and validator_id not in required:
            required.append(validator_id)

        # Remove validator from optional if present (it's now required)
        if validator_id in optional:
            optional.remove(validator_id)

        # Guardrail: max required roles
        if len(required) > config.max_required_roles:
            # Demote lowest-priority required roles to optional
            overflow = required[config.max_required_roles:]
            required = required[: config.max_required_roles]
            optional = overflow + optional

        # Guardrail: max total roles
        max_total = config.max_roles_per_meeting
        total = len(required) + len(optional)
        if total > max_total:
            # Truncate optional roles to fit
            allowed_optional = max_total - len(required)
            if allowed_optional < 0:
                allowed_optional = 0
                # Also truncate required if needed
                required = required[:max_total]
            optional = optional[:allowed_optional]

        # Guardrail: no overlap between required and optional
        required_set = set(required)
        optional = [r for r in optional if r not in required_set]

        return MatchResult(
            agenda_type=result.agenda_type,
            agenda_label=result.agenda_label,
            tags=result.tags,
            risk_tags=result.risk_tags,
            required_roles=tuple(required),
            optional_roles=tuple(optional),
            validator_required=result.validator_required,
            codex_required=result.codex_required,
            priority=result.priority,
            routing_source=result.routing_source,
            routing_reason=result.routing_reason,
            confidence=result.confidence,
            generated_at=result.generated_at,
            version=result.version,
            requires_approval=result.requires_approval,
        )


# ═══════════════════════════════════════════════════════════════════════════
# Convenience function
# ═══════════════════════════════════════════════════════════════════════════


def match_meeting_route(
    topic: str,
    rules_data: dict[str, Any],
    routing_reason: str = "static_fallback",
    context: MeetingContext | None = None,
) -> MatchResult:
    """Convenience function: match a meeting topic to a route.

    Loads the rule config from a raw routing rules dict, creates a
    matcher, and returns the match result.

    Args:
        topic: Raw meeting agenda text.
        rules_data: Parsed routing_rules.yaml dict.
        routing_reason: Why the fallback was activated.
        context: Optional pre-built MeetingContext (overrides topic).

    Returns:
        MatchResult with complete routing information.
    """
    config = RuleConfig.from_dict(rules_data)
    matcher = StaticRuleMatcher(config)

    if context is None:
        context = MeetingContext(topic=topic)

    return matcher.match(context, routing_reason=routing_reason)
