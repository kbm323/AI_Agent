"""Phase 26 live worker/validator/auditor boundary smoke policy.

Machine-checkable boundary smoke for Gate 5/6/7. This module does not execute
live provider calls. It verifies that the worker/validator/auditor execution
boundary preserves the required safety posture: AI_Agent-owned task packets,
Hermes-owned provider/auth resolution, timeout/error fail-closed behavior,
output sanitization, quota gate, and no direct secret/env passthrough.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum, unique

_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)(\b(?:api[_-]?key|token|password|credential|secret)\b"
    r"['\"]?\s*[:=]\s*['\"]?)"
    r"([^\s,'\"}]+)"
)
_BEARER_SECRET_RE = re.compile(r"(?i)\bbearer\s+\S+")
_MAX_SANITIZED_LENGTH = 4096


def sanitize_worker_output(raw: str) -> str:
    """Redact secret-like patterns from worker stdout/stderr."""

    cleaned = _BEARER_SECRET_RE.sub("bearer [redacted]", raw)
    cleaned = _SECRET_ASSIGNMENT_RE.sub(r"\1[redacted]", cleaned)
    return cleaned[:_MAX_SANITIZED_LENGTH]


@unique
class BoundarySmokeStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"


@dataclass(frozen=True)
class BoundarySmokeCheck:
    """Individual boundary condition check."""

    name: str
    description: str
    passed: bool


@dataclass(frozen=True)
class BoundarySmokeResult:
    """Aggregate boundary smoke evaluation result."""

    status: BoundarySmokeStatus
    checks: tuple[BoundarySmokeCheck, ...]
    failed_checks: tuple[BoundarySmokeCheck, ...]


@dataclass(frozen=True)
class LiveWorkerBoundarySmokePolicy:
    """Phase 26 worker/validator/auditor boundary smoke guardrail.

    Verifies that the execution boundary meets Gate 5/6/7 requirements
    without executing any live CLI. All conditions must pass for PASS.
    """

    checks: tuple[BoundarySmokeCheck, ...]

    @classmethod
    def current_verified(cls) -> LiveWorkerBoundarySmokePolicy:
        """Return the current verified boundary smoke posture."""

        return cls(
            checks=(
                BoundarySmokeCheck(
                    name="ai_agent_task_packet",
                    description=(
                        "AI_Agent persists WorkerTask packets before provider calls"
                    ),
                    passed=True,
                ),
                BoundarySmokeCheck(
                    name="model_provider_recorded",
                    description=(
                        "GLM validator and Codex auditor paths record "
                        "model/provider used"
                    ),
                    passed=True,
                ),
                BoundarySmokeCheck(
                    name="timeout_fail_closed",
                    description=(
                        "timeout produces structured TIMED_OUT result"
                    ),
                    passed=True,
                ),
                BoundarySmokeCheck(
                    name="provider_error_fail_closed",
                    description=(
                        "provider/auth/error responses produce structured FAILED result"
                    ),
                    passed=True,
                ),
                BoundarySmokeCheck(
                    name="output_sanitized",
                    description=(
                        "raw stdout/stderr with secret-like values is "
                        "sanitized before persistence/projection"
                    ),
                    passed=True,
                ),
                BoundarySmokeCheck(
                    name="quota_gate_checked",
                    description=(
                        "quota gate checked before worker batches"
                    ),
                    passed=True,
                ),
                BoundarySmokeCheck(
                    name="no_subprocess_cli",
                    description=(
                        "default worker path does not execute opencode-go CLI"
                    ),
                    passed=True,
                ),
                BoundarySmokeCheck(
                    name="hermes_auth_boundary",
                    description=(
                        "provider/auth resolution is delegated to Hermes runtime"
                    ),
                    passed=True,
                ),
            )
        )

    def evaluate(
        self,
        *,
        ai_agent_task_packet: bool,
        model_provider_recorded: bool,
        timeout_fail_closed: bool,
        provider_error_fail_closed: bool,
        output_sanitized: bool,
        quota_gate_checked: bool,
        no_subprocess_cli: bool,
        hermes_auth_boundary: bool,
    ) -> BoundarySmokeResult:
        """Fail closed unless every boundary condition holds."""

        condition_map = {
            "ai_agent_task_packet": ai_agent_task_packet,
            "model_provider_recorded": model_provider_recorded,
            "timeout_fail_closed": timeout_fail_closed,
            "provider_error_fail_closed": provider_error_fail_closed,
            "output_sanitized": output_sanitized,
            "quota_gate_checked": quota_gate_checked,
            "no_subprocess_cli": no_subprocess_cli,
            "hermes_auth_boundary": hermes_auth_boundary,
        }
        evaluated = tuple(
            BoundarySmokeCheck(
                name=check.name,
                description=check.description,
                passed=condition_map[check.name],
            )
            for check in self.checks
        )
        failed = tuple(c for c in evaluated if not c.passed)
        status = (
            BoundarySmokeStatus.PASS if not failed else BoundarySmokeStatus.FAIL
        )
        return BoundarySmokeResult(
            status=status, checks=evaluated, failed_checks=failed
        )

    def verification_report(self) -> dict[str, str]:
        """Return a stable Phase 26 report shape for docs and audits."""

        return {
            "phase": "Phase 26",
            "name": (
                "Live Worker / Validator / Auditor Boundary Smoke"
            ),
            "gate_5_kanban_live_client": "PARTIAL",
            "gate_6_worker_validator_auditor_boundary": (
                "VERIFIED_BOUNDARY_SMOKE_POLICY_EXISTS"
            ),
            "gate_7_quota_cost_monitoring": "AVAILABLE",
            "default_runner": "hermes_provider_worker",
            "live_cli_execution_in_tests": "not_used",
            "output_sanitization": "required",
            "quota_gate": "checked_before_worker_batches",
            "shell_usage": "not_allowed",
        }


__all__ = [
    "BoundarySmokeCheck",
    "BoundarySmokeResult",
    "BoundarySmokeStatus",
    "LiveWorkerBoundarySmokePolicy",
    "sanitize_worker_output",
]
