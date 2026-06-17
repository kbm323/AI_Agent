"""Crash detection and recovery entry (Sub-AC 4.4.4).

On system startup, scans the meetings directory for manifests left in
a non-terminal state by a prior crashed or killed run.  For each
recoverable meeting it builds a :class:`RecoveryPlan` that describes
the correct lifecycle position at which to resume, then either
auto-recovers or queues the meeting for operator review.

Architecture
------------

::

    Meetings directory
         │
         ▼
    scan_for_incomplete_manifests()  ──►  list[MeetingManifest]
         │
         ▼
    classify_recoverability()  ──►  RecoverabilityVerdict
         │
         ▼
    build_recovery_plan()  ──►  RecoveryPlan
         │
         ▼
    recover_meeting()  ──►  RecoveryResult (manifest ready to resume)

Every recovery action is logged to the manifest's ``error_log``
(silent fail forbidden per the Seed constraint).  The recovery
process is idempotent — re-running it against an already-recovered
manifest is a no-op.

Recoverable states
    All :attr:`~src.shared.lifecycle.ACTIVE_STATES` plus
    ``paused``, ``deadlocked``, and ``escalated``.

Non-recoverable terminal states
    ``completed``, ``cancelled``, ``failed``, ``stale``.

Staleness detection
    A manifest that has not been updated for longer than
    ``stale_timeout_seconds`` is classified as stale and is NOT
    auto-recovered (the meeting data is preserved on disk for audit).

Usage::

    from src.crash_recovery import (
        scan_for_incomplete_manifests,
        classify_recoverability,
        build_recovery_plan,
        recover_meeting,
    )

    # On system startup
    incomplete = scan_for_incomplete_manifests("/path/to/meetings")
    for manifest in incomplete:
        verdict = classify_recoverability(manifest)
        if verdict.is_recoverable:
            plan = build_recovery_plan(manifest)
            result = recover_meeting(manifest, plan)
            print(f"Recovered {manifest.meeting_id}: {result.message}")

Modules:
    RecoveryPlan: Immutable recovery instructions for one meeting.
    RecoverabilityVerdict: Verdict with reason.
    RecoveryEntryResult: Outcome of a recovery entry attempt.
    scan_for_incomplete_manifests: Find manifests needing recovery.
    classify_recoverability: Decide if a manifest can be recovered.
    build_recovery_plan: Determine resume position.
    recover_meeting: Execute recovery and return resumable manifest.
    auto_recover_all: Convenience for full startup recovery sweep.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

from src.meeting_trigger import (
    DEFAULT_MEETINGS_ROOT,
    MeetingManifest,
    load_manifest,
    update_manifest,
)
from src.shared.lifecycle import (
    ACTIVE_STATES,
    TERMINAL_STATES,
    LifecycleState,
    is_active,
    is_terminal,
)

# ── Logger ────────────────────────────────────────────────────────────────

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────

DEFAULT_STALE_TIMEOUT_SECONDS = 86_400  # 24 hours
"""Default: manifests untouched for >24h are considered stale."""

RECOVERABLE_STATES = frozenset(
    {
        LifecycleState.CREATED,
        LifecycleState.QUEUED,
        LifecycleState.ROUTING,
        LifecycleState.CONTEXT_RETRIEVAL,
        LifecycleState.IN_MEETING,
        LifecycleState.CONSENSUS_BUILDING,
        LifecycleState.VALIDATING,
        LifecycleState.EXECUTING,
        LifecycleState.FINALIZING,
        LifecycleState.PAUSED,
        LifecycleState.DEADLOCKED,
        LifecycleState.ESCALATED,
    }
)
"""States from which a meeting can be recovered."""


# ── Recovery plan ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RecoveryPlan:
    """Immutable recovery instructions for a single meeting.

    Describes exactly where and how to resume a meeting after a crash.

    Attributes:
        meeting_id: The meeting to recover.
        last_state: The state persisted in the manifest at crash time.
        resume_state: The lifecycle state to resume from (may differ
                      from ``last_state`` when the last persisted step
                      was partial — e.g. a context packet was written
                      but the worker hadn't responded yet).
        round_count: Round number at crash time.
        current_speaker: The speaker that was active at crash time
                         (empty if none).
        speaker_queue: The speaker queue at crash time.
        completed_step: The last fully-completed lifecycle step, used
                        to determine what needs to be re-done vs.
                        picked up mid-stream.
        context_packets_count: Number of context packets already persisted.
        decisions_count: Number of decisions already persisted.
        tool_outputs_count: Number of tool outputs already persisted.
        recovery_note: Human-readable summary of what recovery will do.
        stale: True if the manifest has not been updated recently
               enough to be auto-recovered (operator review needed).
    """

    meeting_id: str
    last_state: str
    resume_state: str
    round_count: int
    current_speaker: str = ""
    speaker_queue: tuple[str, ...] = ()
    completed_step: str = ""
    context_packets_count: int = 0
    decisions_count: int = 0
    tool_outputs_count: int = 0
    recovery_note: str = ""
    stale: bool = False

    @property
    def can_auto_recover(self) -> bool:
        """True if the meeting can be automatically recovered (not stale)."""
        return not self.stale


# ── Recoverability verdict ────────────────────────────────────────────────


class Recoverability(str, Enum):
    """Classification of whether and how a manifest can be recovered.

    Values:
        RECOVERABLE: Meeting is in a valid active state and can be auto-recovered.
        STALE: Meeting has been inactive too long — requires operator review.
        STALE_RECOVERABLE: Meeting is stale but data is intact — operator
                           can choose to recover manually.
        TERMINAL: Meeting is in a terminal state — no recovery needed or possible.
        CORRUPTED: Manifest is present but unreadable or missing required fields.
        ALREADY_RECOVERED: Manifest was already recovered (idempotency guard).
    """

    RECOVERABLE = "recoverable"
    STALE = "stale"
    STALE_RECOVERABLE = "stale_recoverable"
    TERMINAL = "terminal"
    CORRUPTED = "corrupted"
    ALREADY_RECOVERED = "already_recovered"


@dataclass(frozen=True)
class RecoverabilityVerdict:
    """Complete verdict on whether a manifest can be recovered.

    Attributes:
        verdict: The classification.
        reason: Human-readable explanation.
        meeting_id: The meeting this verdict applies to.
        is_recoverable: Convenience — True for RECOVERABLE and
                        STALE_RECOVERABLE.
    """

    verdict: Recoverability
    reason: str
    meeting_id: str = ""

    @property
    def is_recoverable(self) -> bool:
        return self.verdict in (
            Recoverability.RECOVERABLE,
            Recoverability.STALE_RECOVERABLE,
        )

    @property
    def is_auto_recoverable(self) -> bool:
        return self.verdict == Recoverability.RECOVERABLE


# ── Recovery result ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class RecoveryEntryResult:
    """Immutable result of a recovery entry attempt.

    Attributes:
        meeting_id: The meeting that was (or wasn't) recovered.
        success: True if recovery completed without error.
        plan: The recovery plan that was executed, or None if
              recovery was not attempted.
        manifest: The recovered manifest with state updated and
                  recovery event logged, or None on failure.
        message: Human-readable summary.
        error: Exception raised during recovery, or None.
    """

    meeting_id: str
    success: bool
    plan: Optional[RecoveryPlan] = None
    manifest: Optional[MeetingManifest] = None
    message: str = ""
    error: Optional[Exception] = None


# ── Recovery action handler type ──────────────────────────────────────────

RecoveryEntryHandler = Callable[
    [MeetingManifest, RecoveryPlan], RecoveryEntryResult
]
"""Injectable handler for testing recovery execution without disk I/O."""


# ── Internal helpers ──────────────────────────────────────────────────────


def _utc_now() -> datetime:
    """Return the current UTC datetime."""
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return _utc_now().isoformat()


def _manifest_age_seconds(manifest: MeetingManifest) -> float:
    """Return the age of the manifest in seconds since last update.

    Returns a large number if ``updated_at`` is missing or unparseable,
    so that truly ancient (or broken) manifests are treated as stale
    rather than silently auto-recovered.
    """
    if not manifest.updated_at:
        return float("inf")
    try:
        # Handle both 'Z' and '+00:00' ISO formats
        ts_str = manifest.updated_at.replace("Z", "+00:00")
        updated = datetime.fromisoformat(ts_str)
        age = _utc_now() - updated
        return age.total_seconds()
    except (ValueError, TypeError):
        logger.warning(
            "Unparseable updated_at=%r for meeting_id=%s — treating as ancient",
            manifest.updated_at,
            manifest.meeting_id,
        )
        return float("inf")


def _has_recovery_marker(manifest: MeetingManifest) -> bool:
    """Check whether the manifest already carries a recovery event marker."""
    for error_entry in manifest.error_log:
        if error_entry.get("error_type") == "crash_recovery":
            return True
    return False


def _resolve_resume_state(manifest: MeetingManifest) -> str:
    """Determine the correct resume state from the manifest.

    The rule: resume from ``completed_step`` if it is set and is a
    recoverable state; otherwise resume from the current ``state``.

    This handles the case where a transition was persisted but the
    subsequent step never completed — we re-enter at the last known
    good position.
    """
    completed = manifest.completed_step
    if completed and completed in RECOVERABLE_STATES:
        return str(completed)
    # Fall back to current state
    current = manifest.state
    if current in RECOVERABLE_STATES:
        return str(current)
    # If current state is somehow not recoverable (shouldn't happen
    # because classify_recoverability filters first), use completed_step
    # or CREATED as absolute fallback.
    return str(completed or LifecycleState.CREATED)


# ── Public API: scan ──────────────────────────────────────────────────────


def scan_for_incomplete_manifests(
    meetings_root: str | Path | None = None,
    *,
    stale_timeout_seconds: float = DEFAULT_STALE_TIMEOUT_SECONDS,
) -> list[MeetingManifest]:
    """Scan the meetings directory for manifests in non-terminal states.

    Walks the meetings root directory, loads every ``manifest.json``,
    and returns manifests whose state is NOT terminal.

    Manifests that are corrupted (bad JSON, missing fields) are logged
    as warnings and skipped — they do NOT halt the scan.  The caller
    should check the log for ``CORRUPTED`` entries.

    Args:
        meetings_root: Root meetings directory.  Defaults to
                       ``./meetings/`` relative to CWD.
        stale_timeout_seconds: Manifests older than this many seconds
                               are flagged stale but still returned
                               (the caller decides what to do).

    Returns:
        List of manifests in non-terminal states, ordered by
        ``updated_at`` (oldest first) for deterministic recovery.
    """
    if meetings_root is None:
        root = Path.cwd() / DEFAULT_MEETINGS_ROOT
    else:
        root = Path(meetings_root).resolve()

    if not root.is_dir():
        logger.info(
            "Meetings root %s does not exist — nothing to recover", root
        )
        return []

    incomplete: list[MeetingManifest] = []

    for manifest_path in sorted(root.rglob("manifest.json")):
        try:
            manifest = load_manifest(str(manifest_path))
        except Exception as exc:
            logger.warning(
                "Skipping corrupted manifest %s: %s", manifest_path, exc
            )
            continue

        # Skip terminal states — nothing to recover
        if is_terminal(manifest.state):
            logger.debug(
                "Meeting %s is in terminal state %s — skipping",
                manifest.meeting_id,
                manifest.state,
            )
            continue

        incomplete.append(manifest)

    # Sort oldest-first so the longest-abandoned meetings recover first
    incomplete.sort(key=lambda m: m.updated_at or "")

    logger.info(
        "Scan complete: %d meeting(s) found, %d incomplete",
        len(list(root.rglob("manifest.json"))),
        len(incomplete),
    )
    return incomplete


# ── Public API: classify ──────────────────────────────────────────────────


def classify_recoverability(
    manifest: MeetingManifest,
    *,
    stale_timeout_seconds: float = DEFAULT_STALE_TIMEOUT_SECONDS,
) -> RecoverabilityVerdict:
    """Classify whether a manifest can be recovered after a crash.

    Decision logic:

    ====================  ===========================================
    Condition              Verdict
    ====================  ===========================================
    Terminal state         TERMINAL
    Already recovered      ALREADY_RECOVERED
    Active + fresh         RECOVERABLE
    Active + stale         STALE_RECOVERABLE
    Recoverable + stale    STALE (auto-recovery blocked)
    ====================  ===========================================

    Args:
        manifest: The manifest to classify.
        stale_timeout_seconds: Age in seconds after which a manifest
                               is considered stale.

    Returns:
        A ``RecoverabilityVerdict`` with classification and reason.
    """
    meeting_id = manifest.meeting_id
    state = manifest.state

    # ── Terminal states need no recovery ──────────────────────────────
    try:
        if is_terminal(state):
            return RecoverabilityVerdict(
                verdict=Recoverability.TERMINAL,
                reason=(
                    f"Meeting {meeting_id} is in terminal state "
                    f"'{state}' — no recovery needed"
                ),
                meeting_id=meeting_id,
            )
    except ValueError:
        # Invalid state name — fall through to CORRUPTED check below
        pass

    # ── Idempotency: already recovered ────────────────────────────────
    if _has_recovery_marker(manifest):
        return RecoverabilityVerdict(
            verdict=Recoverability.ALREADY_RECOVERED,
            reason=(
                f"Meeting {meeting_id} already has a crash_recovery "
                f"event in error_log — skipping duplicate recovery"
            ),
            meeting_id=meeting_id,
        )

    # ── Check if state is recoverable at all ──────────────────────────
    state_enum: LifecycleState
    try:
        state_enum = LifecycleState(state)
    except ValueError:
        return RecoverabilityVerdict(
            verdict=Recoverability.CORRUPTED,
            reason=(
                f"Meeting {meeting_id} has unknown state "
                f"'{state}' — manifest may be corrupted"
            ),
            meeting_id=meeting_id,
        )

    if state_enum not in RECOVERABLE_STATES:
        return RecoverabilityVerdict(
            verdict=Recoverability.CORRUPTED,
            reason=(
                f"Meeting {meeting_id} state '{state}' is not "
                f"a recognised recoverable state"
            ),
            meeting_id=meeting_id,
        )

    # ── Staleness check ───────────────────────────────────────────────
    age = _manifest_age_seconds(manifest)
    is_stale = age > stale_timeout_seconds

    if is_stale:
        age_hours = age / 3600.0
        return RecoverabilityVerdict(
            verdict=Recoverability.STALE_RECOVERABLE,
            reason=(
                f"Meeting {meeting_id} has been inactive for "
                f"{age_hours:.1f} hours (state='{state}') — "
                f"data is intact but operator review recommended"
            ),
            meeting_id=meeting_id,
        )

    # ── Fresh + active → auto-recoverable ─────────────────────────────
    return RecoverabilityVerdict(
        verdict=Recoverability.RECOVERABLE,
        reason=(
            f"Meeting {meeting_id} in state '{state}' is fresh "
            f"(age={age:.0f}s) and can be auto-recovered"
        ),
        meeting_id=meeting_id,
    )


# ── Public API: build plan ────────────────────────────────────────────────


def build_recovery_plan(
    manifest: MeetingManifest,
    *,
    stale_timeout_seconds: float = DEFAULT_STALE_TIMEOUT_SECONDS,
) -> RecoveryPlan:
    """Build a recovery plan for a recoverable manifest.

    Determines the correct resume state, round, speaker position, and
    describes what the recovery process will do.

    Args:
        manifest: The manifest to build a plan for.  Must have passed
                  ``classify_recoverability`` with a recoverable verdict.
        stale_timeout_seconds: Used to determine staleness.

    Returns:
        A ``RecoveryPlan`` ready to pass to ``recover_meeting``.
    """
    age = _manifest_age_seconds(manifest)
    is_stale = age > stale_timeout_seconds
    resume_state = _resolve_resume_state(manifest)

    # Build a human-readable summary
    parts: list[str] = [
        f"Recover meeting {manifest.meeting_id}",
        f"from state '{manifest.state}'",
    ]
    if resume_state != manifest.state:
        parts.append(f"(resume at '{resume_state}')")
    parts.append(f"round {manifest.round_count}")

    if manifest.current_speaker:
        parts.append(f"speaker={manifest.current_speaker}")
    if manifest.context_packets:
        parts.append(f"context_packets={len(manifest.context_packets)}")
    if manifest.decisions:
        parts.append(f"decisions={len(manifest.decisions)}")

    if is_stale:
        parts.insert(0, "[STALE]")

    return RecoveryPlan(
        meeting_id=manifest.meeting_id,
        last_state=manifest.state,
        resume_state=resume_state,
        round_count=manifest.round_count,
        current_speaker=manifest.current_speaker,
        speaker_queue=manifest.speaker_queue,
        completed_step=manifest.completed_step,
        context_packets_count=len(manifest.context_packets),
        decisions_count=len(manifest.decisions),
        tool_outputs_count=len(manifest.tool_outputs),
        recovery_note="; ".join(parts),
        stale=is_stale,
    )


# ── Public API: recover ───────────────────────────────────────────────────


def recover_meeting(
    manifest: MeetingManifest,
    plan: RecoveryPlan,
    *,
    persist: bool = True,
    on_persist: Optional[
        Callable[[MeetingManifest], MeetingManifest]
    ] = None,
) -> RecoveryEntryResult:
    """Execute recovery for a meeting and return the resumable manifest.

    The recovery process:

    1. Creates a recovery event entry for ``manifest.error_log``.
    2. Sets the manifest state to ``plan.resume_state`` (the correct
       lifecycle position to resume from).
    3. Optionally persists the manifest to disk.
    4. Returns the recovered manifest.

    The operation is idempotent — calling it on an already-recovered
    manifest is safe (the recovery marker check prevents double-logging
    but the state update is still applied).

    Args:
        manifest: The manifest to recover.
        plan: The recovery plan from ``build_recovery_plan``.
        persist: If True, persist the manifest to disk after recovery.
        on_persist: Injectable persistence function for testing.
                    Defaults to ``update_manifest``.

    Returns:
        A ``RecoveryEntryResult`` with the recovered manifest.
    """
    meeting_id = manifest.meeting_id

    # ── Build recovery event ──────────────────────────────────────────
    recovery_event: dict[str, str] = {
        "timestamp": _utc_now_iso(),
        "error_type": "crash_recovery",
        "message": (
            f"Crash recovery executed: resumed at state "
            f"'{plan.resume_state}' (was '{manifest.state}'), "
            f"round {plan.round_count}"
        ),
        "severity": "info",
        "recovery": (
            f"Meeting auto-recovered after crash; "
            f"resume_state={plan.resume_state}"
        ),
    }

    # ── Append recovery event to error_log ────────────────────────────
    manifest_with_event = manifest.with_error(recovery_event)

    # ── Set the correct resume state ──────────────────────────────────
    recovered = manifest_with_event.with_state(plan.resume_state)

    logger.info(
        "Recovery: meeting_id=%s last_state=%s resume_state=%s round=%d",
        meeting_id,
        plan.last_state,
        plan.resume_state,
        plan.round_count,
    )

    # ── Persist ───────────────────────────────────────────────────────
    if persist:
        try:
            persist_fn = on_persist or update_manifest
            recovered = persist_fn(recovered)
        except Exception as exc:
            logger.exception(
                "CRITICAL: Failed to persist recovered manifest for %s",
                meeting_id,
            )
            return RecoveryEntryResult(
                meeting_id=meeting_id,
                success=False,
                plan=plan,
                manifest=recovered,  # Return in-memory state
                message=(
                    f"Recovery applied in memory but persist failed: {exc}"
                ),
                error=exc,
            )

    return RecoveryEntryResult(
        meeting_id=meeting_id,
        success=True,
        plan=plan,
        manifest=recovered,
        message=(
            f"Meeting {meeting_id} recovered: resumed at "
            f"'{plan.resume_state}' (was '{plan.last_state}'), "
            f"round {plan.round_count}"
        ),
    )


# ── Public API: auto recover all ──────────────────────────────────────────


def auto_recover_all(
    meetings_root: str | Path | None = None,
    *,
    stale_timeout_seconds: float = DEFAULT_STALE_TIMEOUT_SECONDS,
    auto_recover_stale: bool = False,
    on_persist: Optional[
        Callable[[MeetingManifest], MeetingManifest]
    ] = None,
) -> list[RecoveryEntryResult]:
    """Full startup recovery sweep — scan, classify, and recover all.

    Convenience function that chains scan → classify → recover for
    every recoverable meeting found under ``meetings_root``.

    Args:
        meetings_root: Root meetings directory.
        stale_timeout_seconds: Staleness threshold.
        auto_recover_stale: If True, also auto-recover stale meetings.
                            Default False (operator review required).
        on_persist: Injectable persistence function for testing.

    Returns:
        List of ``RecoveryEntryResult`` for every meeting processed.
    """
    manifests = scan_for_incomplete_manifests(
        meetings_root,
        stale_timeout_seconds=stale_timeout_seconds,
    )

    results: list[RecoveryEntryResult] = []

    for manifest in manifests:
        verdict = classify_recoverability(
            manifest, stale_timeout_seconds=stale_timeout_seconds
        )

        # Skip terminal and already-recovered
        if verdict.verdict in (
            Recoverability.TERMINAL,
            Recoverability.ALREADY_RECOVERED,
        ):
            results.append(
                RecoveryEntryResult(
                    meeting_id=manifest.meeting_id,
                    success=True,
                    message=verdict.reason,
                )
            )
            continue

        # Skip corrupted
        if verdict.verdict == Recoverability.CORRUPTED:
            logger.error("Corrupted manifest: %s", verdict.reason)
            results.append(
                RecoveryEntryResult(
                    meeting_id=manifest.meeting_id,
                    success=False,
                    message=verdict.reason,
                )
            )
            continue

        # Skip stale unless explicitly allowed
        if (
            verdict.verdict == Recoverability.STALE_RECOVERABLE
            and not auto_recover_stale
        ):
            logger.warning("Stale meeting skipped: %s", verdict.reason)
            results.append(
                RecoveryEntryResult(
                    meeting_id=manifest.meeting_id,
                    success=False,
                    message=verdict.reason,
                )
            )
            continue

        # Build plan and recover
        plan = build_recovery_plan(
            manifest, stale_timeout_seconds=stale_timeout_seconds
        )
        result = recover_meeting(
            manifest, plan, persist=True, on_persist=on_persist
        )
        results.append(result)

    recovered_count = sum(1 for r in results if r.success and r.manifest is not None)
    logger.info(
        "Auto-recovery sweep complete: %d total, %d recovered",
        len(results),
        recovered_count,
    )
    return results
