"""Phase 32/33 live Discord audit helpers.

Phase 32 froze the default thread contract: exactly 12 discussion messages and
no automatic final report/checkpoint.  Phase 33 tightens that into a chaired
meeting protocol: representative first, stable agenda, coherent Round 2, and a
specific final quality/validation message.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

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

PHASE33_ROLE_ORDER: tuple[str, ...] = (
    "ceo_coordinator",
    "content_lead",
    "art_lead",
    "tech_lead",
    "marketing_lead",
    "quality_lead",
    "ceo_coordinator",
    "content_lead",
    "art_lead",
    "tech_lead",
    "marketing_lead",
    "quality_lead",
)

_ROLE_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ceo_coordinator", ("[대표]", "대표")),
    ("content_lead", ("[콘텐츠 팀장]", "콘텐츠팀장", "콘텐츠 팀장")),
    ("art_lead", ("[아트 팀장]", "아트팀장", "아트 팀장")),
    ("tech_lead", ("[기술 팀장]", "기술팀장", "기술 팀장")),
    ("marketing_lead", ("[마케팅 팀장]", "마케팅팀장", "마케팅 팀장")),
    (
        "quality_lead",
        (
            "[검증 팀장]",
            "[품질관리 팀장]",
            "검증팀장",
            "품질관리팀장",
            "검증 팀장",
            "품질관리 팀장",
        ),
    ),
)


class Phase32AuditFailure(AssertionError):  # noqa: N818
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


@dataclass(frozen=True)
class Phase33MeetingProtocolAuditResult:
    ok: bool
    message_count: int
    role_order: tuple[str, ...]
    role_order_ok: bool
    agenda_terms_missing: tuple[str, ...]
    duplicate_round_roles: tuple[str, ...]
    forbidden_markers_found: tuple[str, ...]
    errors: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def audit_phase32_default_thread(
    messages: Sequence[Mapping[str, Any]],
) -> Phase32DefaultThreadAuditResult:
    """Audit the default Phase 32 meeting thread body."""

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


def audit_phase33_default_meeting_protocol(
    messages: Sequence[Mapping[str, Any]],
    *,
    expected_agenda_terms: Sequence[str] = (),
) -> Phase33MeetingProtocolAuditResult:
    """Audit the default thread against the stricter Phase 33 protocol."""

    contents = [_content(m) for m in messages]
    combined = "\n".join(contents)
    forbidden = tuple(
        marker for marker in FORBIDDEN_DEFAULT_THREAD_MARKERS if marker in combined
    )
    role_order = tuple(_message_role(m) for m in messages)
    agenda_missing = tuple(
        term for term in expected_agenda_terms if term and term not in combined
    )
    duplicates = _duplicate_round_roles(role_order, contents)
    role_order_ok = role_order == PHASE33_ROLE_ORDER

    errors: list[str] = []
    if len(contents) != 12:
        errors.append(f"expected 12 default discussion messages, got {len(contents)}")
    if not role_order_ok:
        errors.append("visible role order does not match Phase 33 protocol")
    if forbidden:
        errors.append("default thread contains automatic report/checkpoint markers")
    if agenda_missing:
        errors.append("expected agenda terms are missing from thread")
    if duplicates:
        errors.append("same role repeated Round 1 text in Round 2")
    if contents and not _looks_like_chair_opening(contents[0]):
        errors.append("first message is not representative chair opening")
    if len(contents) >= 7 and not _looks_like_round2_briefing(contents[6]):
        errors.append("message 7 is not representative Round 2 briefing")
    if contents and not _is_validation_round2(contents[-1]):
        errors.append("last message is not validation/quality Round 2")
    if contents and not _quality_round2_is_specific(contents[-1]):
        errors.append("quality Round 2 lacks final validation decision conditions")

    return Phase33MeetingProtocolAuditResult(
        ok=not errors,
        message_count=len(contents),
        role_order=role_order,
        role_order_ok=role_order_ok,
        agenda_terms_missing=agenda_missing,
        duplicate_round_roles=duplicates,
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
        section
        for section in REQUIRED_ON_DEMAND_REPORT_SECTIONS
        if section not in content
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


def _author(message: Mapping[str, Any]) -> str:
    raw = message.get("author", "")
    if isinstance(raw, Mapping):
        raw = raw.get("username", "")
    return raw if isinstance(raw, str) else str(raw)


def _message_role(message: Mapping[str, Any]) -> str:
    author = _author(message)
    content = _content(message)
    for role, markers in _ROLE_MARKERS:
        if any(marker in author for marker in markers):
            return role
    for role, markers in _ROLE_MARKERS:
        bracket_markers = tuple(marker for marker in markers if marker.startswith("["))
        if any(marker in content for marker in bracket_markers):
            return role
    for role, markers in _ROLE_MARKERS:
        if any(marker in content for marker in markers):
            return role
    return "unknown"


def _duplicate_round_roles(
    role_order: tuple[str, ...], contents: list[str]
) -> tuple[str, ...]:
    duplicates: list[str] = []
    for index, role in enumerate(PHASE33_ROLE_ORDER[:6]):
        round2_index = index + 6
        if round2_index >= len(contents) or index >= len(contents):
            continue
        if index >= len(role_order) or round2_index >= len(role_order):
            continue
        if role_order[index] != role or role_order[round2_index] != role:
            continue
        if _normalize(contents[index]) == _normalize(contents[round2_index]):
            duplicates.append(role)
    return tuple(duplicates)


def _normalize(value: str) -> str:
    return " ".join(value.split()).strip().lower()


def _looks_like_chair_opening(content: str) -> bool:
    return "대표" in content and any(
        token in content for token in ("개회", "시작", "안건", "발언 순서")
    )


def _looks_like_round2_briefing(content: str) -> bool:
    return "대표" in content and any(
        token in content for token in ("Round 2", "2라운드", "브리핑", "쟁점", "종합")
    )


def _is_validation_round2(content: str) -> bool:
    has_validation_role = any(
        role in content
        for role in ("[검증 팀장]", "[품질관리 팀장]", "검증 팀장", "품질관리 팀장")
    )
    has_round2 = any(
        token in content for token in ("Round 2", "round 2", "2라운드", "라운드 2")
    )
    return has_validation_role and has_round2


def _quality_round2_is_specific(content: str) -> bool:
    decision_terms = ("승인", "보류", "수정요구", "수정 요구", "조건")
    evidence_terms = ("리스크", "검증", "증거", "안건", "순서", "반복")
    return any(term in content for term in decision_terms) and any(
        term in content for term in evidence_terms
    )
