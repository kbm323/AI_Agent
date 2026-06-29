"""Meeting parameter validator — Sub-AC 1b-ii.

Takes raw slash command option values (topic, team_selection, urgency),
validates and coerces them into structured :class:`MeetingParams`.

Design
------
- **Single entry point** — ``validate_meeting_params()`` receives raw
  option values as scalars and returns a discriminated
  ``MeetingParamsResult`` (success / failure with error list).
- **Pure function** — no filesystem I/O, no CLI calls, no side effects.
  Fully testable with synthetic option value combinations.
- **Validation rules** (per Seed ontology):
  1. *topic* — non-empty string after stripping, max 4000 chars.
  2. *team_selection* — if provided, each entry must be a recognised
     team ID (see ``VALID_TEAM_IDS``).  Duplicates are collapsed.
     Empty selection is valid (means "all teams available").
  3. *urgency* — must be one of ``p0``, ``p1``, ``p2``, ``p3``.
     Defaults to ``p2`` when not provided.
- **Coercion** — whitespace stripping, case-normalisation, and
  deduplication are applied before validation.
- **Composable** — can be called upstream of ``trigger_input_parser``
  or used independently for unit-testing slash command option handling.

Usage::

    from src.meeting_params_validator import validate_meeting_params

    result = validate_meeting_params(
        topic="신규 캐릭터 디자인 검토",
        team_selection=["art-design", "content-production"],
        urgency="p1",
    )
    if result.success:
        print(result.params.topic)        # '신규 캐릭터 디자인 검토'
        print(result.params.team_selection)  # ('art-design', 'content-production')
        print(result.params.urgency)      # 'p1'
    else:
        for err in result.errors:
            print(f"[{err.code}] {err.message}")
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# ═══════════════════════════════════════════════════════════════════════════
# Constants — valid teams and urgency tiers
# ═══════════════════════════════════════════════════════════════════════════

#: Recognised team IDs from routing_rules.yaml / Seed ontology.
#: These are the six teams in the AI Virtual Entertainment Company.
VALID_TEAM_IDS: frozenset[str] = frozenset(
    {
        "content-production",
        "art-design",
        "tech-engineering",
        "marketing",
        "validation",
        "execution",
    }
)
"""Valid team identifiers. Each maps to a team leader role (e.g.
``content-production`` → ``content-director``)."""

#: Valid urgency / priority tiers.
VALID_URGENCY_TIERS: frozenset[str] = frozenset({"p0", "p1", "p2", "p3"})
"""Accepted urgency tiers: p0 (blocking), p1 (high), p2 (normal), p3 (low)."""

#: Maximum characters for a meeting topic (Discord message limit is 2000,
#: but we allow longer for combined webhook / direct API sources).
MAX_TOPIC_LENGTH: int = 4000
"""Maximum length of a meeting topic string."""


# ═══════════════════════════════════════════════════════════════════════════
# Data types
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class MeetingParams:
    """Validated and coerced meeting parameters.

    Produced by ``validate_meeting_params()`` on success.  All fields
    are guaranteed to satisfy the Seed ontology constraints for a
    meeting initiation request.

    Attributes:
        topic: Non-empty, stripped meeting topic string.
        team_selection: Deduplicated, sorted tuple of valid team IDs.
            Empty tuple means "no team filter" (all teams available).
        urgency: Normalised urgency tier: ``p0`` | ``p1`` | ``p2`` | ``p3``.
    """

    topic: str
    """Non-empty meeting topic, stripped of leading/trailing whitespace."""

    team_selection: tuple[str, ...] = ()
    """Validated team IDs. Empty means no team filter applied."""

    urgency: str = "p2"
    """Priority tier: p0 (blocking), p1 (high), p2 (normal), p3 (low)."""

    def __post_init__(self) -> None:
        """Validate field constraints on construction.

        Raises:
            ValueError: If any field violates constraints.  Callers
                should use ``validate_meeting_params()`` instead of
                constructing ``MeetingParams`` directly to get
                structured error reporting.
        """
        if not self.topic or not self.topic.strip():
            raise ValueError("topic must not be empty")
        if len(self.topic) > MAX_TOPIC_LENGTH:
            raise ValueError(
                f"topic exceeds {MAX_TOPIC_LENGTH} characters "
                f"({len(self.topic)})"
            )
        if self.urgency not in VALID_URGENCY_TIERS:
            raise ValueError(
                f"invalid urgency '{self.urgency}' — "
                f"must be one of: {', '.join(sorted(VALID_URGENCY_TIERS))}"
            )
        for team_id in self.team_selection:
            if team_id not in VALID_TEAM_IDS:
                raise ValueError(
                    f"invalid team_id '{team_id}' — "
                    f"must be one of: {', '.join(sorted(VALID_TEAM_IDS))}"
                )

    def to_dict(self) -> dict[str, object]:
        """Serialize to a plain dictionary for logging/storage."""
        return {
            "topic": self.topic,
            "team_selection": list(self.team_selection),
            "urgency": self.urgency,
        }


# ═══════════════════════════════════════════════════════════════════════════
# Error types
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class MeetingParamsError:
    """A single validation error from ``validate_meeting_params()``.

    Attributes:
        code: Machine-readable error code (e.g. ``"EMPTY_TOPIC"``).
        message: Human-readable description in English/Korean.
        field: The parameter field name that caused the error
            (``"topic"``, ``"team_selection"``, ``"urgency"``).
    """

    code: str
    """Machine-readable error code."""

    message: str
    """Human-readable error description."""

    field: str
    """The field name that failed validation."""


# ═══════════════════════════════════════════════════════════════════════════
# Validation result — discriminated union
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class MeetingParamsResult:
    """Result of validating meeting parameters.

    Discriminated union — check ``success`` to narrow:

    - ``True`` → ``params`` holds the validated ``MeetingParams``.
    - ``False`` → ``errors`` holds one or more ``MeetingParamsError`` entries.

    This mirrors the ``ParseResult`` / ``DispatchResult`` pattern used
    throughout the AI_Agent system.
    """

    success: bool
    """True when all parameters passed validation."""

    params: Optional[MeetingParams] = None
    """The validated parameters (success only)."""

    errors: tuple[MeetingParamsError, ...] = ()
    """Validation errors (failure only). One entry per failed validation rule."""


# ═══════════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════════


def validate_meeting_params(
    topic: str,
    team_selection: Optional[list[str]] = None,
    urgency: str = "p2",
) -> MeetingParamsResult:
    """Validate raw meeting parameter values from slash command options.

    Receives raw option values as they arrive from Discord's slash
    command interaction (already parsed by the command argument parser)
    and produces a validated, coerced ``MeetingParams`` object.

    **Validation order** (all errors collected before returning):

    1. **topic** — strip whitespace; reject empty; reject over-length.
    2. **team_selection** — strip each entry; reject unrecognised team IDs;
       deduplicate; sort for determinism.
    3. **urgency** — strip and lowercase; reject invalid tiers;
       default to ``p2`` when empty.

    Args:
        topic: Raw topic string from slash command ``agenda`` option.
        team_selection: Optional list of team ID strings.  ``None`` or
            empty list means "no team filter".
        urgency: Raw urgency string, defaults to ``"p2"``.

    Returns:
        ``MeetingParamsResult`` — inspect ``.success``:
        - ``True`` → access ``.params`` (``MeetingParams``).
        - ``False`` → inspect ``.errors`` (``tuple[MeetingParamsError, ...]``).

    Examples:
        >>> result = validate_meeting_params("캐릭터 디자인 검토")
        >>> result.success
        True
        >>> result.params.topic
        '캐릭터 디자인 검토'
        >>> result.params.urgency
        'p2'

        >>> result = validate_meeting_params("", urgency="p5")
        >>> result.success
        False
        >>> [e.code for e in result.errors]
        ['EMPTY_TOPIC', 'INVALID_URGENCY']
    """
    errors: list[MeetingParamsError] = []

    # ── 1. Validate topic ──────────────────────────────────────────
    cleaned_topic = topic.strip() if topic else ""

    if not cleaned_topic:
        errors.append(
            MeetingParamsError(
                code="EMPTY_TOPIC",
                message="Meeting topic must not be empty",
                field="topic",
            )
        )
    elif len(cleaned_topic) > MAX_TOPIC_LENGTH:
        errors.append(
            MeetingParamsError(
                code="TOPIC_TOO_LONG",
                message=(
                    f"Meeting topic must be at most {MAX_TOPIC_LENGTH} "
                    f"characters (got {len(cleaned_topic)})"
                ),
                field="topic",
            )
        )

    # ── 2. Validate urgency ────────────────────────────────────────
    cleaned_urgency = urgency.strip().lower() if urgency else "p2"

    if not cleaned_urgency:
        cleaned_urgency = "p2"

    if cleaned_urgency not in VALID_URGENCY_TIERS:
        errors.append(
            MeetingParamsError(
                code="INVALID_URGENCY",
                message=(
                    f"Invalid urgency tier '{cleaned_urgency}' — "
                    f"must be one of: {', '.join(sorted(VALID_URGENCY_TIERS))}"
                ),
                field="urgency",
            )
        )

    # ── 3. Validate team_selection ─────────────────────────────────
    validated_teams: tuple[str, ...] = ()

    if team_selection:
        seen: set[str] = set()
        raw_teams: list[str] = []

        for raw_team in team_selection:
            if not isinstance(raw_team, str):
                errors.append(
                    MeetingParamsError(
                        code="INVALID_TEAM_TYPE",
                        message=(
                            f"Team selection entry must be a string, "
                            f"got {type(raw_team).__name__}"
                        ),
                        field="team_selection",
                    )
                )
                continue

            stripped = raw_team.strip().lower()
            if not stripped:
                errors.append(
                    MeetingParamsError(
                        code="EMPTY_TEAM_ENTRY",
                        message="Team selection entry must not be empty",
                        field="team_selection",
                    )
                )
                continue

            if stripped not in VALID_TEAM_IDS:
                errors.append(
                    MeetingParamsError(
                        code="INVALID_TEAM_ID",
                        message=(
                            f"Invalid team ID '{stripped}' — "
                            f"must be one of: {', '.join(sorted(VALID_TEAM_IDS))}"
                        ),
                        field="team_selection",
                    )
                )
                continue

            if stripped not in seen:
                seen.add(stripped)
                raw_teams.append(stripped)

        # Sort for deterministic output order.
        validated_teams = tuple(sorted(raw_teams))

    # ── Aggregate ──────────────────────────────────────────────────
    if errors:
        return MeetingParamsResult(
            success=False,
            params=None,
            errors=tuple(errors),
        )

    return MeetingParamsResult(
        success=True,
        params=MeetingParams(
            topic=cleaned_topic,
            team_selection=validated_teams,
            urgency=cleaned_urgency,
        ),
        errors=(),
    )


# ═══════════════════════════════════════════════════════════════════════════
# Convenience type guards
# ═══════════════════════════════════════════════════════════════════════════


def is_params_success(
    result: MeetingParamsResult,
) -> "bool":
    """Type guard: returns True when result holds valid MeetingParams.

    Example::

        if is_params_success(result):
            # result.params is narrowed to MeetingParams
            print(result.params.topic)
    """
    return result.success


def is_params_error(
    result: MeetingParamsResult,
) -> "bool":
    """Type guard: returns True when result holds validation errors.

    Example::

        if is_params_error(result):
            for err in result.errors:
                print(f"[{err.code}] {err.message}")
    """
    return not result.success


def format_params_errors(
    result: MeetingParamsResult,
) -> str:
    """Format validation errors from a failed result as a human-readable string.

    Returns an empty string for successful results.

    Args:
        result: A ``MeetingParamsResult`` (success or failure).

    Returns:
        Multi-line string of errors, or ``""`` if validation passed.
    """
    if result.success:
        return ""
    lines: list[str] = []
    for err in result.errors:
        lines.append(f"[{err.code}] {err.field}: {err.message}")
    return "\n".join(lines)
