"""Phase 32 live Discord audit regression tests."""

from __future__ import annotations

import json

from src.runtime_architecture_v2.phase32_live_audit import (
    FORBIDDEN_DEFAULT_THREAD_MARKERS,
    Phase32AuditFailure,
    audit_phase32_default_thread,
    audit_phase32_on_demand_report,
    phase32_audit_summary,
)


def _default_messages() -> list[dict[str, str]]:
    roles = [
        "대표",
        "콘텐츠 팀장",
        "아트 팀장",
        "기술 팀장",
        "마케팅 팀장",
        "검증 팀장",
    ]
    return [
        {"id": f"m{i:02d}", "content": f"**[{role}]** Round {round_no} 발언"}
        for round_no in (1, 2)
        for i, role in enumerate(roles, start=1)
    ]


def test_default_thread_audit_passes_for_12_team_lead_messages() -> None:
    result = audit_phase32_default_thread(_default_messages())

    assert result.ok is True
    assert result.message_count == 12
    assert result.last_is_validation_round2 is True
    assert result.forbidden_markers_found == ()


def test_default_thread_audit_rejects_auto_final_report_marker() -> None:
    messages = _default_messages() + [
        {"id": "m13", "content": "# 📋 최종보고서\n## 🎯 결론\n## ✅ 합의안"}
    ]

    result = audit_phase32_default_thread(messages)

    assert result.ok is False
    assert result.message_count == 13
    assert "# 📋" in result.forbidden_markers_found
    assert "## 🎯 결론" in result.forbidden_markers_found
    assert "## ✅ 합의안" in result.forbidden_markers_found


def test_default_thread_audit_requires_validation_round2_as_last_message() -> None:
    messages = _default_messages()
    messages[-1] = {"id": "m12", "content": "**[마케팅 팀장]** Round 2 발언"}

    result = audit_phase32_default_thread(messages)

    assert result.ok is False
    assert result.last_is_validation_round2 is False
    assert "last message is not validation/quality Round 2" in result.errors


def test_on_demand_report_audit_passes_for_v3_discord_body() -> None:
    content = """# 📋 최종보고서: 쿠폰 회의

## 🎯 결론
음성 사연 동의와 쿠폰 중복 차단을 출시 조건으로 진행한다.

## ✅ 합의안
• 동의 기록이 있을 때만 쿠폰에 연결한다.
• 쿠폰 사용 전/완료 상태를 구분한다.

## 🚀 다음 액션
• 기술팀: 쿠폰 중복 사용을 차단한다.
• 법무/검증: 음성 사연 동의와 약관을 검토한다.

## ⚠️ 리스크
• 동의 누락 시 개인정보/저작권 리스크가 생긴다.

## 🔍 검증
검증 PASS · fallback 없음 · 상세 evidence는 local artifact 보관
"""

    result = audit_phase32_on_demand_report(content)

    assert result.ok is True
    assert result.length <= 1600
    assert result.required_sections_present is True
    assert result.forbidden_markers_found == ()


def test_on_demand_report_audit_rejects_internal_model_markers() -> None:
    content = "# 📋 최종보고서\n## 🎯 결론\ndeepseek model evidence runtime artifact"

    result = audit_phase32_on_demand_report(content)

    assert result.ok is False
    assert "deepseek" in result.forbidden_markers_found
    assert "model evidence" in result.forbidden_markers_found
    assert "runtime artifact" in result.forbidden_markers_found


def test_phase32_audit_summary_is_json_serializable() -> None:
    summary = phase32_audit_summary(
        default_thread=audit_phase32_default_thread(_default_messages()),
        on_demand_report=audit_phase32_on_demand_report("# 📋 최종보고서\n## 🎯 결론\n## ✅ 합의안\n## 🚀 다음 액션\n## ⚠️ 리스크\n## 🔍 검증"),
    )

    payload = json.loads(json.dumps(summary, ensure_ascii=False))
    assert payload["default_thread"]["ok"] is True
    assert payload["on_demand_report"]["ok"] is True
    assert "# 📋" in FORBIDDEN_DEFAULT_THREAD_MARKERS
    assert Phase32AuditFailure.__name__ == "Phase32AuditFailure"
