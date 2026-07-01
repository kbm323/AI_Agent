"""Phase 32 live Discord audit helpers.

Stage 6 freezes the live-audit contract in code so a real Discord thread body
can be checked consistently after manual or scripted live runs.

The module intentionally separates two checks:
1. Default meeting thread: exactly 12 discussion messages, no automatic report.
2. Explicit on-demand report: v3 report body is short, user-facing, and clean.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence


FORBIDDEN_DEFAULT_THREAD_MARKERS: tuple[str, ...] = (
    "# 📋",
    "## 🎯 결론",
    "## ✅ 합의안",
    "## 🚀 다음 액션",
    "회의 체크포인트",
)

FORBIDDEN_ON_DEMAND_REPORT_MARKERS: tuple[str, ...] = (
    "model evidence",
    "deepseek",
    "qwen",
    "glm",
    "runtime artifact",
    "worker_execution_failed",
    "placeholder output",
    "Discord thread",
)

REQUIRED_ON_DEMAND_REPORT_SECTIONS: tuple[str, ...] = (
    "# 📋 최종보고서",
    "## 🎯 결론",
    "## ✅ 합의안",
    "## 🚀 다음 액션",
    "## ⚠️ 리스크",
    "## 🔍 검증",
)


class Phase32AuditFailure(AssertionError):
    """Raised by callers that want exception-style live audit failures."""


@dataclass(frozen=True)
class Phase32DefaultThreadAuditResult:
    ok: bool
    message_count: int
    last_is_validation_round2: bool
    forbidden_markers_found: tuple[str, ...]
    errors: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Phase32OnDemandReportAuditResult:
    ok: bool
    length: int
    required_sections_present: bool
    forbidden_markers_found: tuple[str, ...]
    errors: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def audit_phase32_default_thread(
    messages: Sequence[Mapping[str, Any]],
) -> Phase32DefaultThreadAuditResult:
    """Audit the default meeting thread body.

    ``messages`` should be in chronological order (oldest first), matching how
    humans read a Discord meeting thread. Each item needs a ``content`` key.
    """
    contents = [_content(m) for m in messages]
    combined = "\n".join(contents)
    forbidden = tuple(
        marker for marker in FORBIDDEN_DEFAULT_THREAD_MARKERS if marker in combined
    )

    errors: list[str] = []
    if len(contents) != 12:
        errors.append(f"expected 12 default discussion messages, got {len(contents)}")
    if forbidden:
        errors.append("default thread contains automatic report/checkpoint markers")

    last = contents[-1] if contents else ""
    last_is_validation = _is_validation_round2(last)
    if not last_is_validation:
        errors.append("last message is not validation/quality Round 2")

    return Phase32DefaultThreadAuditResult(
        ok=not errors,
        message_count=len(contents),
        last_is_validation_round2=last_is_validation,
        forbidden_markers_found=forbidden,
        errors=tuple(errors),
    )


def audit_phase32_on_demand_report(content: str) -> Phase32OnDemandReportAuditResult:
    """Audit an explicitly requested v3 report body before/after Discord post."""
    lower = content.lower()
    forbidden = tuple(
        marker
        for marker in FORBIDDEN_ON_DEMAND_REPORT_MARKERS
        if marker.lower() in lower
    )
    missing = tuple(
        section for section in REQUIRED_ON_DEMAND_REPORT_SECTIONS if section not in content
    )
    errors: list[str] = []
    if len(content) > 1600:
        errors.append(f"on-demand report exceeds 1600 chars: {len(content)}")
    if missing:
        errors.append("on-demand report is missing required v3 sections")
    if forbidden:
        errors.append("on-demand report contains internal/model markers")

    return Phase32OnDemandReportAuditResult(
        ok=not errors,
        length=len(content),
        required_sections_present=not missing,
        forbidden_markers_found=forbidden,
        errors=tuple(errors),
    )


def phase32_audit_summary(
    *,
    default_thread: Phase32DefaultThreadAuditResult,
    on_demand_report: Phase32OnDemandReportAuditResult | None = None,
) -> dict[str, Any]:
    """Return JSON-serializable Phase 32 audit summary."""
    payload: dict[str, Any] = {
        "ok": default_thread.ok and (on_demand_report.ok if on_demand_report else True),
        "default_thread": default_thread.to_dict(),
    }
    if on_demand_report is not None:
        payload["on_demand_report"] = on_demand_report.to_dict()
    return payload


def assert_phase32_audit_passed(summary: Mapping[str, Any]) -> None:
    """Raise Phase32AuditFailure if a summary indicates failure."""
    if not summary.get("ok"):
        raise Phase32AuditFailure(str(summary))


def _content(message: Mapping[str, Any]) -> str:
    value = message.get("content", "")
    return value if isinstance(value, str) else str(value)


def _is_validation_round2(content: str) -> bool:
    has_validation_role = any(
        role in content for role in ("[검증 팀장]", "[품질관리 팀장]", "검증 팀장", "품질관리 팀장")
    )
    has_round2 = any(token in content for token in ("Round 2", "round 2", "2라운드", "라운드 2"))
    return has_validation_role and has_round2
