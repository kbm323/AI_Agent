"""Phase 32/33 live Discord audit regression tests."""

from __future__ import annotations

import json

from src.runtime_architecture_v2.phase32_live_audit import (
    FORBIDDEN_DEFAULT_THREAD_MARKERS,
    Phase32AuditFailure,
    audit_phase32_default_thread,
    audit_phase32_on_demand_report,
    audit_phase33_default_meeting_protocol,
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


def _phase33_message(author: str, content: str) -> dict[str, str]:
    return {"author": author, "content": content}


def _phase33_messages() -> list[dict[str, str]]:
    return [
        _phase33_message(
            "대표",
            "**[대표]** Round 1 개회: Phase33 회의 진행 품질 검증 "
            "안건을 확인하고 발언 순서를 안내합니다.",
        ),
        _phase33_message(
            "콘텐츠팀장",
            "**[콘텐츠 팀장]** Round 1: Phase33 회의 진행 품질을 "
            "콘텐츠 관점에서 점검합니다.",
        ),
        _phase33_message(
            "아트팀장",
            "**[아트 팀장]** Round 1: Phase33 안건의 사용자-facing "
            "표현 품질을 검토합니다.",
        ),
        _phase33_message(
            "기술팀장",
            "**[기술 팀장]** Round 1: Phase33 안건 유지와 발언 순서 "
            "구현 경계를 검토합니다.",
        ),
        _phase33_message(
            "마케팅팀장",
            "**[마케팅 팀장]** Round 1: Phase33 회의 UX가 사용자의 "
            "신뢰에 미치는 영향을 검토합니다.",
        ),
        _phase33_message(
            "품질관리팀장",
            "**[품질관리 팀장]** Round 1 검증 기준: 안건 유지, "
            "순서 준수, 반복 방지 리스크를 확인합니다.",
        ),
        _phase33_message(
            "대표",
            "**[대표]** Round 2 브리핑: 1라운드 쟁점인 안건 유지와 "
            "품질관리 기준을 종합합니다.",
        ),
        _phase33_message(
            "콘텐츠팀장",
            "**[콘텐츠 팀장]** Round 2: 기술팀의 안건 유지 구현 의견에 "
            "동의하고 콘텐츠 기준을 보완합니다.",
        ),
        _phase33_message(
            "아트팀장",
            "**[아트 팀장]** Round 2: 콘텐츠팀 의견을 반영해 "
            "사용자-facing 표현 조건을 보완합니다.",
        ),
        _phase33_message(
            "기술팀장",
            "**[기술 팀장]** Round 2: 대표 브리핑을 반영해 prompt "
            "전파와 순서 고정을 구현 조건으로 제시합니다.",
        ),
        _phase33_message(
            "마케팅팀장",
            "**[마케팅 팀장]** Round 2: 품질관리 기준을 반영해 "
            "신뢰 훼손 리스크를 줄이는 조건을 제안합니다.",
        ),
        _phase33_message(
            "품질관리팀장",
            "**[품질관리 팀장]** Round 2 최종 검증: 수정요구 조건은 "
            "안건 유지, 순서 준수, 반복 방지 증거입니다.",
        ),
    ]


def test_default_thread_audit_passes_for_12_team_lead_messages() -> None:
    result = audit_phase32_default_thread(_default_messages())

    assert result.ok is True
    assert result.message_count == 12
    assert result.last_is_validation_round2 is True
    assert result.forbidden_markers_found == ()


def test_phase33_protocol_audit_accepts_chair_led_thread() -> None:
    result = audit_phase33_default_meeting_protocol(
        _phase33_messages(),
        expected_agenda_terms=("Phase33", "회의 진행 품질"),
    )

    assert result.ok is True
    assert result.role_order_ok is True
    assert result.agenda_terms_missing == ()
    assert result.duplicate_round_roles == ()


def test_phase33_protocol_audit_rejects_content_before_chair() -> None:
    messages = _phase33_messages()
    messages[0], messages[1] = messages[1], messages[0]

    result = audit_phase33_default_meeting_protocol(messages)

    assert result.ok is False
    assert result.role_order_ok is False
    assert "visible role order does not match Phase 33 protocol" in result.errors


def test_phase33_protocol_audit_rejects_agenda_drift() -> None:
    messages = []
    for message in _phase33_messages():
        messages.append(
            {
                **message,
                "content": message["content"]
                .replace("Phase33", "신규 버추얼 아이돌")
                .replace("회의 진행 품질", "데뷔 컨셉"),
            }
        )

    result = audit_phase33_default_meeting_protocol(
        messages,
        expected_agenda_terms=("Phase33", "회의 진행 품질"),
    )

    assert result.ok is False
    assert "Phase33" in result.agenda_terms_missing
    assert "expected agenda terms are missing from thread" in result.errors


def test_phase33_protocol_audit_rejects_duplicate_quality_rounds() -> None:
    messages = _phase33_messages()
    duplicate = "**[품질관리 팀장]** Round 2 의견: 추가 검토가 필요합니다."
    messages[5] = {"author": "품질관리팀장", "content": duplicate}
    messages[11] = {"author": "품질관리팀장", "content": duplicate}

    result = audit_phase33_default_meeting_protocol(messages)

    assert result.ok is False
    assert "quality_lead" in result.duplicate_round_roles
    assert "same role repeated Round 1 text in Round 2" in result.errors


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
    report = "\n".join(
        [
            "# 📋 최종보고서",
            "## 🎯 결론",
            "## ✅ 합의안",
            "## 🚀 다음 액션",
            "## ⚠️ 리스크",
            "## 🔍 검증",
        ]
    )
    summary = phase32_audit_summary(
        default_thread=audit_phase32_default_thread(_default_messages()),
        on_demand_report=audit_phase32_on_demand_report(report),
    )

    payload = json.loads(json.dumps(summary, ensure_ascii=False))
    assert payload["default_thread"]["ok"] is True
    assert payload["on_demand_report"]["ok"] is True
    assert "# 📋" in FORBIDDEN_DEFAULT_THREAD_MARKERS
    assert Phase32AuditFailure.__name__ == "Phase32AuditFailure"
