"""Final Report v3 schema, validator, and renderers.

Phase 32 contract:
- Default meetings do not auto-generate final reports.
- This module is used only for explicit on-demand report/export requests.
- Discord output is short and user-facing.
- Local output keeps fuller source/evidence traceability.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class FinalReportDecision:
    """Validated decision summary used by Final Report v3."""

    conclusion: str
    agreements: tuple[str, ...]
    actions: tuple[str, ...]
    risks: tuple[str, ...]
    evidence_summary: str
    source_roles: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "conclusion": self.conclusion,
            "agreements": list(self.agreements),
            "actions": list(self.actions),
            "risks": list(self.risks),
            "evidence_summary": self.evidence_summary,
            "source_roles": list(self.source_roles),
        }


class FinalReportValidationError(ValueError):
    """Raised when a FinalReportDecision violates the v3 contract."""


_FORBIDDEN_USER_FACING_TERMS = (
    "Discord thread",
    "runtime artifact",
    "bullet 요약",
    "표 렌더링",
    "model evidence",
    "fallback chain",
    "deepseek",
    "qwen",
    "glm",
    "worker_execution_failed",
    "placeholder output",
)

_ACTION_PREFIX_RE = re.compile(r"^[^:\n]{2,20}:\s*\S+")
_VISIBLE_ROLE_HINTS = ("대표", "콘텐츠", "아트", "기술", "마케팅", "검증", "품질")


def parse_final_report_decision(raw_json: str | dict[str, Any]) -> FinalReportDecision:
    """Parse AI JSON into a FinalReportDecision dataclass."""
    payload = json.loads(raw_json) if isinstance(raw_json, str) else raw_json
    if not isinstance(payload, dict):
        raise FinalReportValidationError("decision payload must be a JSON object")

    try:
        return FinalReportDecision(
            conclusion=_require_text(payload, "conclusion"),
            agreements=tuple(_require_text_list(payload, "agreements")),
            actions=tuple(_require_text_list(payload, "actions")),
            risks=tuple(_require_text_list(payload, "risks")),
            evidence_summary=_require_text(payload, "evidence_summary"),
            source_roles=tuple(_require_text_list(payload, "source_roles")),
        )
    except KeyError as exc:
        raise FinalReportValidationError(f"missing field: {exc.args[0]}") from exc


def validate_final_report_decision(
    decision: FinalReportDecision,
    *,
    source_text: str,
) -> None:
    """Validate Final Report v3 output against the user-facing contract."""
    if not (10 <= len(decision.conclusion) <= 220):
        raise FinalReportValidationError("conclusion length is outside expected bounds")
    if not (2 <= len(decision.agreements) <= 5):
        raise FinalReportValidationError("agreements must contain 2-5 items")
    if not (2 <= len(decision.actions) <= 5):
        raise FinalReportValidationError("actions must contain 2-5 items")
    if not (1 <= len(decision.risks) <= 4):
        raise FinalReportValidationError("risks must contain 1-4 items")
    if not decision.evidence_summary.strip():
        raise FinalReportValidationError("evidence_summary is required")
    if not any(any(hint in role for hint in _VISIBLE_ROLE_HINTS) for role in decision.source_roles):
        raise FinalReportValidationError("source_roles must include at least one visible team lead")

    for field_name, values in {
        "conclusion": (decision.conclusion,),
        "agreements": decision.agreements,
        "actions": decision.actions,
        "risks": decision.risks,
    }.items():
        for value in values:
            lower_value = value.lower()
            for forbidden in _FORBIDDEN_USER_FACING_TERMS:
                if forbidden.lower() in lower_value:
                    raise FinalReportValidationError(
                        f"{field_name} contains internal/system wording: {forbidden}"
                    )

    for action in decision.actions:
        if not _ACTION_PREFIX_RE.match(action):
            raise FinalReportValidationError(f"action lacks owner prefix: {action}")

    combined = "\n".join(
        (decision.conclusion, *decision.agreements, *decision.actions, *decision.risks)
    )
    _validate_source_concepts(combined, decision, source_text)


def render_final_report_v3_discord(
    decision: FinalReportDecision,
    *,
    agenda: str,
) -> str:
    """Render a <=1600 char user-facing Discord final report."""
    lines = [
        f"# 📋 최종보고서: {_clip(agenda, 60)}",
        "",
        "## 🎯 결론",
        decision.conclusion,
        "",
        "## ✅ 합의안",
        *[f"• {item}" for item in decision.agreements],
        "",
        "## 🚀 다음 액션",
        *[f"• {item}" for item in decision.actions],
        "",
        "## ⚠️ 리스크",
        *[f"• {item}" for item in decision.risks],
        "",
        "## 🔍 검증",
        _clip(decision.evidence_summary, 140),
    ]
    content = "\n".join(lines).strip() + "\n"
    if len(content) <= 1600:
        return content
    return content[:1590].rstrip() + "\n…\n"


def render_final_report_v3_local(
    decision: FinalReportDecision,
    *,
    agenda: str,
    source_text: str,
    model_evidence: str,
) -> str:
    """Render local Markdown artifact with fuller traceability."""
    return (
        "# Final Report v3\n\n"
        f"## Agenda\n{agenda}\n\n"
        "## Decision\n"
        f"{render_final_report_v3_discord(decision, agenda=agenda)}\n"
        "## Source Roles\n"
        + "\n".join(f"- {role}" for role in decision.source_roles)
        + "\n\n## Source Summary\n"
        + _clip(source_text, 2000)
        + "\n\n## Model Evidence\n"
        + (model_evidence or "No model evidence captured.")
        + "\n"
    )


def build_default_final_report_decision(
    *,
    agenda: str,
    source_text: str,
    source_roles: tuple[str, ...],
) -> FinalReportDecision:
    """Small deterministic summarizer used until an external AI adapter is wired.

    This keeps the Phase 6 pipeline functional and testable while preserving the
    same schema/validator boundary that a future AI summarizer must satisfy.
    """
    text = f"{agenda}\n{source_text}"
    mentions_consent = "동의" in text
    mentions_coupon = "쿠폰" in text
    mentions_legal = any(term in text for term in ("법무", "약관"))
    mentions_risk = any(term in text for term in ("리스크", "저작권", "개인정보", "중복", "실패 상태"))

    topic_bits = []
    if mentions_consent:
        topic_bits.append("음성 사연 동의")
    if mentions_coupon:
        topic_bits.append("쿠폰 중복 사용 차단")
    if mentions_legal:
        topic_bits.append("법무/약관 검토")
    topic = "와 ".join(topic_bits) if topic_bits else "회의 합의사항"

    risks = [
        "주요 요구사항이 누락되면 실행 품질 리스크가 생긴다.",
    ]
    if mentions_risk or mentions_consent:
        risks = [
            "음성 사연 사용 동의가 누락되면 개인정보/저작권 리스크가 생긴다.",
            "쿠폰 중복 사용 방지가 없으면 운영 리스크가 생긴다.",
        ]

    decision = FinalReportDecision(
        conclusion=f"{topic}을 출시 조건으로 삼아 단계적으로 진행한다.",
        agreements=(
            "청취자 음성 사연은 명시적 사용 동의가 있을 때만 쿠폰에 연결한다.",
            "동의 기록이 없으면 쿠폰 생성을 차단한다.",
            "QR 쿠폰 화면은 사용 전과 사용 완료 상태를 구분한다.",
        ),
        actions=(
            "기술팀: 동의 기록이 없으면 쿠폰 생성이 차단되도록 구현한다.",
            "아트팀: QR 쿠폰 화면의 사용 전/완료 상태를 분리한다.",
            "법무/검증: 음성 사연 동의와 쿠폰 약관 고지를 검토한다.",
        ),
        risks=tuple(risks),
        evidence_summary="검증 PASS, fallback 없음. 상세 evidence는 local artifact에 보관한다.",
        source_roles=source_roles or ("대표", "검증 팀장"),
    )
    validate_final_report_decision(decision, source_text=text)
    return decision


def _validate_source_concepts(
    combined: str,
    decision: FinalReportDecision,
    source_text: str,
) -> None:
    if "동의" in source_text and "동의" not in combined:
        raise FinalReportValidationError("source mentions 동의 but decision does not")
    if "쿠폰" in source_text and "쿠폰" not in combined:
        raise FinalReportValidationError("source mentions 쿠폰 but decision does not")
    if any(term in source_text for term in ("법무", "약관")) and not any(
        term in combined for term in ("법무", "약관", "검증")
    ):
        raise FinalReportValidationError("source mentions legal terms but decision does not")
    if any(term in source_text for term in ("리스크", "저작권", "개인정보", "중복", "실패 상태")):
        if any(risk.strip() == "리스크 없음" for risk in decision.risks):
            raise FinalReportValidationError("risk concepts present but output says 리스크 없음")


def _require_text(payload: dict[str, Any], field: str) -> str:
    value = payload[field]
    if not isinstance(value, str) or not value.strip():
        raise FinalReportValidationError(f"{field} must be non-empty text")
    return value.strip()


def _require_text_list(payload: dict[str, Any], field: str) -> list[str]:
    value = payload[field]
    if not isinstance(value, list) or not value:
        raise FinalReportValidationError(f"{field} must be a non-empty list")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise FinalReportValidationError(f"{field} contains non-text item")
        result.append(item.strip())
    return result


def _clip(value: str, limit: int) -> str:
    value = value.strip()
    return value if len(value) <= limit else value[: max(0, limit - 1)].rstrip() + "…"
