"""Security, quota, and observability policies for Runtime Architecture v2.

Phase 8 keeps these as deterministic, injectable policy objects. They do not
call provider dashboards, Discord, models, or Hermes runtime APIs directly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .schemas import MeetingRun

_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api[_-]?key|api[_-]?token|token|secret|password|bearer)\b\s*[:=]\s*\S+"
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+\S+")


@dataclass(frozen=True)
class PolicyDecision:
    """Deterministic allow/pause decision for runtime gates."""

    allowed: bool
    reason: str
    safe_summary: str
    next_state: str = "active"
    severity: str = "info"

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "safe_summary": self.safe_summary,
            "next_state": self.next_state,
            "severity": self.severity,
        }


@dataclass(frozen=True)
class QuotaSnapshot:
    """Provider quota snapshot supplied by an external checker."""

    provider: str
    monthly_percent: int
    weekly_percent: int
    hourly_percent: int


class SecurityPolicy:
    """Fail-closed input security gate for MeetingRun triggers."""

    def evaluate(self, run: MeetingRun) -> PolicyDecision:
        trigger_text = str(run.trigger.get("text", ""))
        safe_text = redact_sensitive_text(trigger_text)
        if safe_text != trigger_text:
            return PolicyDecision(
                allowed=False,
                reason="secret_like_input_detected",
                safe_summary=safe_text,
                next_state="paused",
                severity="warning",
            )
        return PolicyDecision(
            allowed=True,
            reason="security_allowed",
            safe_summary="input passed deterministic security policy",
        )


class QuotaPolicy:
    """Fail-closed provider quota gate using an injected snapshot."""

    def __init__(self, *, snapshot: QuotaSnapshot | None = None) -> None:
        self.snapshot = snapshot

    def evaluate(self, *, active_provider: str) -> PolicyDecision:
        if self.snapshot is None:
            return PolicyDecision(
                allowed=True,
                reason="quota_snapshot_unavailable",
                safe_summary=(
                    "no quota snapshot supplied; local deterministic path allowed"
                ),
            )
        if self.snapshot.provider != active_provider:
            return PolicyDecision(
                allowed=True,
                reason="quota_snapshot_for_inactive_provider",
                safe_summary=(
                    f"snapshot for {self.snapshot.provider}; "
                    f"active provider {active_provider}"
                ),
            )
        checks = (
            ("monthly", self.snapshot.monthly_percent),
            ("weekly", self.snapshot.weekly_percent),
            ("hourly", self.snapshot.hourly_percent),
        )
        for window, percent in checks:
            if percent >= 97:
                return PolicyDecision(
                    allowed=False,
                    reason=f"quota_{window}_critical",
                    safe_summary=f"{active_provider} {window} {percent}% >= 97%",
                    next_state="paused",
                    severity="warning",
                )
        return PolicyDecision(
            allowed=True,
            reason="quota_allowed",
            safe_summary="active provider quota below critical thresholds",
        )


class ObservabilityPolicy:
    """Build redacted structured observability events."""

    def event(
        self,
        run: MeetingRun,
        *,
        stage: str,
        outcome: str,
        severity: str = "info",
        detail: str = "",
    ) -> dict[str, Any]:
        return {
            "event": "observability_event",
            "meeting_run_id": run.meeting_run_id,
            "stage": stage,
            "outcome": outcome,
            "severity": severity,
            "detail": redact_sensitive_text(detail),
        }


def redact_sensitive_text(text: str) -> str:
    """Redact secret-like assignments and bearer tokens from policy output."""

    redacted = _SECRET_ASSIGNMENT_RE.sub(
        lambda match: _redact_assignment(match.group(0)),
        text,
    )
    return _BEARER_RE.sub("Bearer [REDACTED]", redacted)


def _redact_assignment(value: str) -> str:
    separator = "=" if "=" in value else ":"
    key = value.split(separator, 1)[0].strip()
    return f"{key}{separator}[REDACTED]"


__all__ = [
    "ObservabilityPolicy",
    "PolicyDecision",
    "QuotaPolicy",
    "QuotaSnapshot",
    "SecurityPolicy",
    "redact_sensitive_text",
]
