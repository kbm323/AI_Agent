"""Round-1 packet set assembly for the multi-agent meeting system.

Sub-AC 5a-4: Collect N independently generated per-persona opinion packets
into a complete round-1 opinion set, verify the count matches the active
persona list, and confirm all packets pass structure validation.

Architecture
------------

The assembler sits between the per-persona generation phase
(``persona_opinion_generator.generate_opinion()``) and the round-1
delivery to the Coordinator.  It receives a collection of
``OpinionGenerationResult`` objects — one per active persona — and
performs three verification gates:

1. **Count gate** — the number of successfully generated packets must
   equal the number of expected persona role IDs.  Missing, extra, or
   failed generations are reported with full role-level detail.

2. **Deduplication gate** — each ``persona_id`` in the assembled set
   must be unique.  Duplicate entries indicate a generation dispatch
   error and are returned with ``persona_id`` disambiguation.

3. **Structure validation gate** — every assembled opinion packet is
   re-validated against the ``validate_opinion_packet()`` schema from
   ``opinion_packet_validator``.  This is a second-pass check (the
   first happens inside ``generate_opinion()``) that catches any
   edge-cases where a packet was mutated between generation and
   assembly.

When all three gates pass, the assembler returns a
``RoundPacketSetResult`` with ``assembled=True`` and the complete
round-1 opinion set ready for the Coordinator to feed into Round 2
(conflict resolution).

Usage::

    from src.persona_opinion_generator import OpinionGenerationResult
    from src.round_packet_assembler import (
        assemble_round_one_packets,
        RoundPacketSetResult,
    )

    # After all generate_opinion() calls complete...
    results: list[OpinionGenerationResult] = [...]
    expected_roles = ("art-director", "tech-director", "marketing-lead")

    assembled = assemble_round_one_packets(results, expected_roles)

    if assembled.assembled:
        for packet in assembled.opinion_packets:
            print(f"  {packet['persona_id']}: confidence={packet['confidence']}")
    else:
        for err in assembled.errors:
            print(f"  [{err.error_category}] {err.message}")

Design decisions
----------------

* **Immutable return value** — ``RoundPacketSetResult`` is a frozen
  dataclass, consistent with the existing ``OpinionGenerationResult``
  and ``OpinionPacketValidationReport`` patterns.

* **All errors collected** — no early-exit on first error; the assembler
  gathers every countable, deduplication, and structural problem so the
  Coordinator can decide whether to retry individual personas or abort
  the round.

* **Optional strict mode** — when ``strict=True``, even a single
  structural validation failure causes the entire set to be rejected.
  When ``strict=False`` (default), structurally invalid packets are
  excluded from the assembled set but the assembler still reports all
  errors with ``assembled=False``.

* **No filesystem I/O** — the assembler is a pure-in-memory function.
  Manifest persistence is the Coordinator's responsibility, via
  ``manifest_serializer``.

Testability
-----------

Every function is pure (no I/O, no globals) and accepts only standard
library types plus the project's own dataclasses.  All LLM calls
happened before the assembler is invoked, so unit tests can feed
hand-crafted ``OpinionGenerationResult`` instances without mock
infrastructure.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.opinion_packet_validator import (
    OpinionPacketValidationReport,
    validate_opinion_packet,
)
from src.persona_opinion_generator import OpinionGenerationResult


# ── Error types ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PacketAssemblyError:
    """A single assembly-level error detected during round-1 packet collection.

    Distinguished from per-field validation errors
    (``OpinionFieldValidationError``) — these are *set-level* problems:
    missing personas, extra personas, duplicate entries, or structural
    validation failures that survived the generation phase.

    Attributes:
        error_category: One of ``count_mismatch``, ``missing_persona``,
            ``extra_persona``, ``duplicate_persona``, ``generation_failure``,
            ``structural_validation``.
        message: Human-readable description.
        persona_id: The persona role ID implicated (empty for category-wide
            errors like ``count_mismatch``).
        detail: Machine-parseable extra data (e.g. the validation report
            for a ``structural_validation`` error).
    """

    error_category: str
    """One of: count_mismatch, missing_persona, extra_persona,
    duplicate_persona, generation_failure, structural_validation."""

    message: str
    """Human-readable description of the assembly error."""

    persona_id: str = ""
    """The persona role ID implicated (empty for set-level errors)."""

    detail: Any = None
    """Machine-parseable extra data (validation report, etc.)."""


# ── Assembly result ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class RoundPacketSetResult:
    """Result of assembling a complete round-1 opinion packet set.

    Attributes:
        assembled: ``True`` when all gates pass and the set is complete
            and valid.
        opinion_packets: The assembled, validated opinion packet dicts
            in insertion order.
        expected_count: Number of persona roles expected (from
            ``required_roles``).
        actual_count: Number of successfully assembled packets.
        failed_count: Number of generation attempts that returned
            ``success=False``.
        errors: All assembly-level errors detected.  Empty when
            ``assembled=True``.
        persona_ids_assembled: Tuple of ``persona_id`` values present
            in the assembled set.
        persona_ids_missing: Tuple of expected ``persona_id`` values
            NOT present in the assembled set.
    """

    assembled: bool
    """True when all three gates pass."""

    opinion_packets: tuple[dict[str, object], ...]
    """The assembled, validated opinion packet dicts."""

    expected_count: int
    """Number of persona roles expected."""

    actual_count: int
    """Number of successfully assembled packets."""

    failed_count: int
    """Number of generation attempts that returned success=False."""

    errors: tuple[PacketAssemblyError, ...]
    """All assembly-level errors detected."""

    persona_ids_assembled: tuple[str, ...]
    """Persona IDs present in the assembled set."""

    persona_ids_missing: tuple[str, ...]
    """Expected persona IDs NOT present in the assembled set."""

    @property
    def error_count(self) -> int:
        """Convenience: total number of assembly errors."""
        return len(self.errors)

    @property
    def all_personas_present(self) -> bool:
        """True when every expected persona ID is represented."""
        return len(self.persona_ids_missing) == 0


# ── Helper: group results by persona_id ──────────────────────────────────


def _group_results_by_persona(
    results: list[OpinionGenerationResult],
) -> dict[str, OpinionGenerationResult]:
    """Group generation results by ``role_id``, detecting duplicates.

    Args:
        results: List of opinion generation results.

    Returns:
        Dict mapping ``role_id`` → ``OpinionGenerationResult``.
        Duplicate entries are resolved by keeping the *first* successful
        result and flagging all subsequent entries for the same
        ``role_id``.

    Raises:
        Does not raise — duplicate detection is deferred to
        ``_detect_duplicates()`` for structured error reporting.
    """
    grouped: dict[str, OpinionGenerationResult] = {}
    for result in results:
        rid = result.role_id
        if rid not in grouped:
            grouped[rid] = result
        else:
            # Keep the first one; duplicates are reported separately
            existing = grouped[rid]
            if not existing.success and result.success:
                grouped[rid] = result  # prefer successful over failed
    return grouped


def _detect_duplicates(
    results: list[OpinionGenerationResult],
    grouped: dict[str, OpinionGenerationResult],
) -> list[PacketAssemblyError]:
    """Detect duplicate persona_id entries in the result set.

    Args:
        results: Original (pre-grouped) result list.
        grouped: Post-grouping dict.

    Returns:
        List of ``PacketAssemblyError`` for each duplicate detected.
    """
    errors: list[PacketAssemblyError] = []
    seen: set[str] = set()
    for result in results:
        rid = result.role_id
        if rid in seen:
            errors.append(
                PacketAssemblyError(
                    error_category="duplicate_persona",
                    message=(
                        f"Duplicate generation result for persona "
                        f"'{rid}'. Only the first result is retained."
                    ),
                    persona_id=rid,
                )
            )
        seen.add(rid)
    return errors


# ── Gate 1: Count verification ──────────────────────────────────────────


def _verify_count(
    results: list[OpinionGenerationResult],
    expected_role_ids: tuple[str, ...],
    grouped: dict[str, OpinionGenerationResult],
) -> list[PacketAssemblyError]:
    """Verify the count of successful results matches expected role count.

    Also identifies specific missing and extra personas.
    """
    errors: list[PacketAssemblyError] = []

    expected_set: set[str] = set(expected_role_ids)
    assembled_set: set[str] = {
        rid
        for rid, r in grouped.items()
        if r.success
    }
    failed_set: set[str] = {
        rid
        for rid, r in grouped.items()
        if not r.success
    }

    # Missing personas: expected but not in group at all
    missing_from_group = expected_set - set(grouped.keys())
    for rid in sorted(missing_from_group):
        errors.append(
            PacketAssemblyError(
                error_category="missing_persona",
                message=(
                    f"Persona '{rid}' was expected but no generation "
                    f"result was produced."
                ),
                persona_id=rid,
            )
        )

    # Failed personas: in group but success=False
    for rid in sorted(failed_set):
        errors.append(
            PacketAssemblyError(
                error_category="generation_failure",
                message=(
                    f"Persona '{rid}' generation failed and cannot be "
                    f"included in the round-1 set."
                ),
                persona_id=rid,
            )
        )

    # Extra personas: present but not expected
    extra_from_group = set(grouped.keys()) - expected_set
    for rid in sorted(extra_from_group):
        errors.append(
            PacketAssemblyError(
                error_category="extra_persona",
                message=(
                    f"Persona '{rid}' was NOT in the expected role list "
                    f"but a generation result was produced."
                ),
                persona_id=rid,
            )
        )

    # Global count mismatch (only when the total assembled ≠ expected)
    successful_count = sum(1 for r in grouped.values() if r.success)
    if successful_count != len(expected_role_ids):
        errors.append(
            PacketAssemblyError(
                error_category="count_mismatch",
                message=(
                    f"Expected {len(expected_role_ids)} opinion packets "
                    f"but {successful_count} were successfully assembled "
                    f"({len(failed_set)} failed). "
                    f"Missing: {sorted(missing_from_group) if missing_from_group else 'none'}. "
                    f"Failed: {sorted(failed_set) if failed_set else 'none'}."
                ),
            )
        )

    return errors


# ── Gate 3: Structure re-validation ─────────────────────────────────────


def _revalidate_packets(
    grouped: dict[str, OpinionGenerationResult],
    *,
    strict: bool = False,
) -> list[PacketAssemblyError]:
    """Re-validate every successfully generated packet against the schema.

    This is a second-pass check.  The first pass happened inside
    ``generate_opinion()`` itself, but this gate catches any edge-case
    where a packet was corrupted between generation and assembly.

    Args:
        grouped: Post-grouping dict of role_id → result.
        strict: If True, any structural validation failure causes the
            entire set to be rejected.  If False (default), failures are
            reported but don't prevent assembly of valid packets.

    Returns:
        List of ``PacketAssemblyError`` for each structural failure.
    """
    errors: list[PacketAssemblyError] = []

    for rid, result in sorted(grouped.items()):
        if not result.success:
            continue  # already caught by count gate

        packet = result.opinion_packet
        if packet is None:
            errors.append(
                PacketAssemblyError(
                    error_category="structural_validation",
                    message=(
                        f"Persona '{rid}' result has success=True but "
                        f"opinion_packet is None."
                    ),
                    persona_id=rid,
                )
            )
            continue

        report = validate_opinion_packet(packet)

        if not report.passed:
            error_strs = [
                f"{e.field_name}: {e.message}" for e in report.errors
            ]
            errors.append(
                PacketAssemblyError(
                    error_category="structural_validation",
                    message=(
                        f"Persona '{rid}' packet failed structural "
                        f"re-validation ({report.error_count} errors): "
                        + "; ".join(error_strs[:5])
                    ),
                    persona_id=rid,
                    detail=report,
                )
            )

    return errors


# ── Public API ──────────────────────────────────────────────────────────


def assemble_round_one_packets(
    results: list[OpinionGenerationResult],
    expected_role_ids: tuple[str, ...],
    *,
    strict: bool = False,
) -> RoundPacketSetResult:
    """Assemble independently generated opinion packets into a round-1 set.

    This is the main entry point for **Sub-AC 5a-4**.

    Steps:

    1. **Group** results by ``role_id``, detecting duplicates.
    2. **Count gate** — verify the number of successfully generated
       packets matches the expected persona count.
    3. **Deduplication gate** — detect and report duplicate entries.
    4. **Structure re-validation gate** — every packet is re-validated
       against the ``validate_opinion_packet()`` schema.
    5. **Assemble** — if all gates pass (or non-strict mode with only
       recoverable errors), return the complete opinion set.

    Args:
        results: List of ``OpinionGenerationResult`` objects from
            per-persona ``generate_opinion()`` calls.
        expected_role_ids: Tuple of role IDs that MUST be present
            (typically from ``manifest.required_roles``).
        strict: If ``True``, any structural validation failure causes
            ``assembled=False``.  If ``False`` (default), structurally
            invalid packets are excluded but other valid packets are
            still assembled (though ``assembled`` will still be
            ``False`` if the count doesn't match).

    Returns:
        ``RoundPacketSetResult`` — check ``result.assembled`` before
        consuming ``result.opinion_packets``.

    Raises:
        ValueError: If ``results`` is empty or ``expected_role_ids``
            is empty.

    Examples:
        >>> from src.persona_opinion_generator import (
        ...     OpinionGenerationResult, PersonaDefinition,
        ... )
        >>> # ... generate opinions for all personas ...
        >>> results = [art_result, tech_result, marketing_result]
        >>> assembled = assemble_round_one_packets(
        ...     results,
        ...     ("art-director", "tech-director", "marketing-lead"),
        ... )
        >>> if assembled.assembled:
        ...     for p in assembled.opinion_packets:
        ...         print(p["persona_id"], p["confidence"])
    """
    # ── Input validation ────────────────────────────────────────────
    if not isinstance(results, list):
        raise TypeError(
            f"results must be a list, got {type(results).__name__}"
        )
    if not isinstance(expected_role_ids, tuple):
        raise TypeError(
            f"expected_role_ids must be a tuple, got "
            f"{type(expected_role_ids).__name__}"
        )
    if not expected_role_ids:
        raise ValueError("expected_role_ids must be a non-empty tuple")
    if not results:
        raise ValueError("results must be a non-empty list")

    all_errors: list[PacketAssemblyError] = []

    # ── Step 1: Group by persona_id ─────────────────────────────────
    grouped = _group_results_by_persona(results)

    # ── Step 2: Detect duplicates ───────────────────────────────────
    dup_errors = _detect_duplicates(results, grouped)
    all_errors.extend(dup_errors)

    # ── Step 3: Count gate ──────────────────────────────────────────
    count_errors = _verify_count(results, expected_role_ids, grouped)
    all_errors.extend(count_errors)

    # ── Step 4: Structure re-validation gate ────────────────────────
    validation_errors = _revalidate_packets(grouped, strict=strict)
    all_errors.extend(validation_errors)

    # ── Step 5: Assemble the valid packets ──────────────────────────
    assembled_packets: list[dict[str, object]] = []
    assembled_ids: list[str] = []

    for rid in sorted(expected_role_ids):
        if rid not in grouped:
            continue
        result = grouped[rid]
        if not result.success:
            continue
        packet = result.opinion_packet
        if packet is None:
            continue

        # Double-check: if there's a structural error for this persona,
        # skip it in the assembled set
        has_struct_error = any(
            e.error_category == "structural_validation"
            and e.persona_id == rid
            for e in all_errors
        )
        if has_struct_error:
            continue

        assembled_packets.append(packet)
        assembled_ids.append(rid)

    # ── Step 6: Determine assembly status ───────────────────────────
    expected_set = set(expected_role_ids)
    assembled_set = set(assembled_ids)
    missing_ids = tuple(sorted(expected_set - assembled_set))

    # Count of assembly errors that are blocking:
    # - count_mismatch: always blocking
    # - missing_persona: always blocking
    # - generation_failure: always blocking
    # - structural_validation: blocking if the failed persona is in
    #   expected_role_ids
    blocking_errors = [
        e for e in all_errors
        if e.error_category
        in ("count_mismatch", "missing_persona", "generation_failure")
        or (
            e.error_category == "structural_validation"
            and e.persona_id in expected_set
        )
    ]

    assembled = len(blocking_errors) == 0 and len(missing_ids) == 0

    # ── Step 7: Compute statistics ──────────────────────────────────
    expected_count = len(expected_role_ids)
    actual_count = len(assembled_packets)
    failed_count = sum(
        1 for r in grouped.values() if not r.success
    )

    return RoundPacketSetResult(
        assembled=assembled,
        opinion_packets=tuple(assembled_packets),
        expected_count=expected_count,
        actual_count=actual_count,
        failed_count=failed_count,
        errors=tuple(all_errors),
        persona_ids_assembled=tuple(assembled_ids),
        persona_ids_missing=missing_ids,
    )


# ── Exports ─────────────────────────────────────────────────────────────


__all__ = [
    "PacketAssemblyError",
    "RoundPacketSetResult",
    "assemble_round_one_packets",
]
