"""Transition-triggered persistence hook (Sub-AC 4.4.3).

Attaches a hook to the state machine that automatically invokes manifest
serialization on every state transition: round advance, speaker change,
decision commit, context-packet append, tool-output append, and lifecycle
state transitions.

Architecture
------------

A **transition hook** is a callable ``(MeetingManifest, str) -> MeetingManifest``
that receives the manifest after a state mutation and the *transition type*
string describing what kind of transition occurred.  Hooks can perform
side-effects (persistence, logging, notifications) but must return the
(possibly modified) manifest.

The module maintains a global hook registry.  Any code that mutates
meeting state should call ``dispatch_transition_hooks()`` *after* the
in-memory mutation but *before* any external calls, satisfying the
Seed constraint: *"All state transitions persist to manifest.json
before external calls."*

The built-in ``persistence_hook`` writes the manifest to disk via
atomic write.  It is installed by ``install_default_hooks()``, which
should be called once during system initialisation.

Transition types (strings passed to hooks)
    state_change       — Lifecycle state transition (execute_transition)
    speaker_change     — Current speaker updated (set_speaker)
    decision_commit    — Decision appended (append_decision)
    context_packet     — Context packet appended (append_context_packet)
    tool_output        — Tool output appended (append_tool_output)
    round_advance      — Round number incremented

Usage::

    from src.transition_persistence_hook import (
        register_transition_hook,
        dispatch_transition_hooks,
        persistence_hook,
        install_default_hooks,
    )

    # Install the built-in persistence hook at startup
    install_default_hooks()

    # After mutating state, dispatch hooks before external calls:
    manifest = dispatch_transition_hooks(manifest, "state_change")
    # Now safe to make external calls — manifest is on disk

    # Register a custom hook (e.g. for metrics)
    def metrics_hook(manifest, transition_type):
        increment_counter(f"transition.{transition_type}")
        return manifest

    register_transition_hook(metrics_hook)
"""

from __future__ import annotations

import logging
from typing import Callable

from src.meeting_trigger import MeetingManifest, update_manifest

# ── Logger ────────────────────────────────────────────────────────────────

logger = logging.getLogger(__name__)

# ── Type alias ────────────────────────────────────────────────────────────

TransitionHook = Callable[[MeetingManifest, str], MeetingManifest]
"""A transition hook function signature.

Args:
    manifest: The manifest **after** the state mutation has been applied.
    transition_type: A string describing the kind of transition
                     (one of: state_change, speaker_change, decision_commit,
                     context_packet, tool_output, round_advance, custom).

Returns:
    The manifest, possibly modified by the hook.
    Hooks must return the manifest even if they only perform side-effects.
"""

# ── Hook registry ─────────────────────────────────────────────────────────

_transition_hooks: list[TransitionHook] = []


def register_transition_hook(hook: TransitionHook) -> None:
    """Register a hook to be invoked on every state transition.

    Hooks are called in registration order.  The same hook can be
    registered multiple times — it will be called once per registration.

    Args:
        hook: A callable matching the ``TransitionHook`` signature.
    """
    _transition_hooks.append(hook)
    logger.debug(
        "Registered transition hook: %s (total=%d)",
        getattr(hook, "__name__", "unnamed"),
        len(_transition_hooks),
    )


def remove_transition_hook(hook: TransitionHook) -> None:
    """Remove a previously registered transition hook.

    Raises ``ValueError`` if the hook is not in the registry.

    Args:
        hook: The hook function to remove.
    """
    _transition_hooks.remove(hook)
    logger.debug(
        "Removed transition hook: %s (total=%d)",
        getattr(hook, "__name__", "unnamed"),
        len(_transition_hooks),
    )


def clear_transition_hooks() -> None:
    """Remove all registered hooks.

    Exposed for testing — production code should not typically clear hooks.
    """
    _transition_hooks.clear()
    logger.debug("Cleared all transition hooks")


def list_transition_hooks() -> list[str]:
    """Return the names of all registered transition hooks."""
    return [getattr(h, "__name__", repr(h)) for h in _transition_hooks]


# ── Hook dispatch ─────────────────────────────────────────────────────────


def dispatch_transition_hooks(
    manifest: MeetingManifest,
    transition_type: str = "generic",
) -> MeetingManifest:
    """Dispatch all registered hooks against the manifest.

    Hooks are called in registration order.  Each hook receives the
    manifest returned by the previous hook (pipeline pattern).

    When **no hooks are registered**, baseline persistence is guaranteed
    via ``update_manifest()`` so that existing callers that don't install
    hooks still get the Seed-mandated persistence.  When hooks ARE
    registered, baseline persistence is the hooks' responsibility
    (typically via the built-in ``persistence_hook``).

    If a hook raises an exception, the error is logged and the hook
    pipeline continues with the next hook — the manifest from the
    previous successful hook is passed forward.  Transition hooks
    are best-effort side-effects; failures in one hook should not
    block subsequent hooks or the overall transition.

    Args:
        manifest: The manifest after state mutation, before persistence.
        transition_type: Type of transition that triggered the dispatch
                         (one of the documented transition type strings).

    Returns:
        The manifest after all hooks have been applied (or after
        baseline persistence when no hooks are registered).
    """
    if not _transition_hooks:
        # Baseline persistence fallback: guarantees the Seed constraint
        # is satisfied even when no custom hooks are installed.
        # Exceptions are NOT caught here — they propagate to the caller
        # (e.g. execute_transition) which logs them to the manifest's
        # error_log and marks the transition as failed.
        return update_manifest(manifest)

    current = manifest
    for i, hook in enumerate(_transition_hooks):
        try:
            current = hook(current, transition_type)
        except Exception as exc:
            hook_name = getattr(hook, "__name__", f"unnamed_{i}")
            logger.exception(
                "Transition hook #%d (%s) failed for transition_type=%s "
                "meeting_id=%s: %s",
                i,
                hook_name,
                transition_type,
                manifest.meeting_id,
                exc,
            )
            # Continue with the manifest as-is (don't roll back)
    return current


# ── Built-in persistence hook ─────────────────────────────────────────────


def persistence_hook(
    manifest: MeetingManifest,
    transition_type: str,
) -> MeetingManifest:
    """Built-in hook: persist the manifest to disk on every transition.

    This hook satisfies the Seed constraint *"All state transitions
    persist to manifest.json before external calls."*  It writes the
    manifest to disk via ``update_manifest()`` (atomic write) and
    returns the updated manifest with a fresh ``updated_at`` timestamp.

    The hook is safe to call even when the manifest has not changed —
    it will still update the timestamp, which serves as a heartbeat
    for crash-recovery liveness detection.

    Args:
        manifest: The manifest to persist.
        transition_type: Type of transition (for logging).

    Returns:
        A new ``MeetingManifest`` with ``updated_at`` refreshed after
        the atomic write.
    """
    logger.debug(
        "Persistence hook: transition_type=%s meeting_id=%s state=%s",
        transition_type,
        manifest.meeting_id,
        manifest.state,
    )
    try:
        return update_manifest(manifest)
    except Exception:
        logger.exception(
            "CRITICAL: Persistence hook failed for meeting_id=%s "
            "transition_type=%s — manifest NOT persisted",
            manifest.meeting_id,
            transition_type,
        )
        # Return the original manifest — the caller can detect that
        # persistence failed and handle accordingly.
        return manifest


# ── Initialisation ────────────────────────────────────────────────────────


def install_default_hooks() -> int:
    """Install the built-in persistence hook.

    Safe to call multiple times — the persistence hook is only
    registered once (idempotent).

    Returns:
        The number of hooks installed (0 or 1).
    """
    if persistence_hook not in _transition_hooks:
        register_transition_hook(persistence_hook)
        logger.info("Default persistence hook installed")
        return 1
    logger.debug("Default persistence hook already installed — skipping")
    return 0
