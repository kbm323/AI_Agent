"""Final Report v3 on-demand schema/renderer tests (Phase 32 / Phase 6)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.runtime_architecture_v2.final_report_v3 import (
    FinalReportDecision,
    FinalReportValidationError,
    parse_final_report_decision,
    render_final_report_v3_discord,
    render_final_report_v3_local,
    validate_final_report_decision,
)
from src.runtime_architecture_v2.multi_bot import run_phase14_multi_bot_pilot
from src.runtime_architecture_v2.on_demand_exports import (
    OnDemandExportType,
    run_on_demand_export,
)


def _valid_payload() -> dict[str, object]:
    return {
        "conclusion": "음성 사연 동의와 쿠폰 중복 사용 차단을 출시 조건으로 삼아 진행한다.",
        "agreements": [
            "청취자 음성 사연은 명시적 사용 동의가 있을 때만 쿠폰에 연결한다.",
            "동의 기록이 없으면 쿠폰 생성을 차단한다.",
            "QR 쿠폰 화면은 사용 전과 사용 완료 상태를 구분한다.",
        ],
        "actions": [
            "기술팀: 동의 기록이 없으면 쿠폰 생성이 차단되도록 구현한다.",
            "아트팀: QR 쿠폰 화면의 사용 전/완료 상태를 분리한다.",
            "법무/검증: 음성 사연 동의와 쿠폰 약관 고지를 검토한다.",
        ],
        "risks": [
            "음성 사연 사용 동의가 누락되면 개인정보/저작권 리스크가 생긴다.",
            "쿠폰 중복 사용 방지가 없으면 운영 리스크가 생긴다.",
        ],
        "evidence_summary": "검증 PASS, fallback 없음. 상세 evidence는 local artifact에 보관한다.",
        "source_roles": ["대표", "콘텐츠 팀장", "아트 팀장", "기술 팀장", "마케팅 팀장", "검증 팀장"],
    }


def test_parse_final_report_decision_from_json() -> None:
    decision = parse_final_report_decision(json.dumps(_valid_payload(), ensure_ascii=False))

    assert isinstance(decision, FinalReportDecision)
    assert "동의" in decision.conclusion
    assert len(decision.agreements) == 3
    assert decision.actions[0].startswith("기술팀:")
    assert decision.source_roles[-1] == "검증 팀장"


def test_validate_final_report_decision_rejects_internal_wording() -> None:
    payload = _valid_payload()
    payload["conclusion"] = "Discord thread runtime artifact를 기준으로 결정한다."
    decision = parse_final_report_decision(json.dumps(payload, ensure_ascii=False))

    with pytest.raises(FinalReportValidationError):
        validate_final_report_decision(decision, source_text="동의 쿠폰 법무 리스크")


def test_validate_final_report_decision_requires_source_concepts() -> None:
    payload = _valid_payload()
    payload["agreements"] = ["팀장들은 출시를 진행하기로 했다.", "화면은 나중에 조정한다."]
    payload["actions"] = ["기술팀: 구현한다.", "아트팀: 화면을 만든다."]
    payload["risks"] = ["리스크 없음"]
    decision = parse_final_report_decision(json.dumps(payload, ensure_ascii=False))

    with pytest.raises(FinalReportValidationError):
        validate_final_report_decision(
            decision,
            source_text="음성 사연 동의, 쿠폰 중복 사용, 법무 약관, 개인정보 리스크를 검토했다.",
        )


def test_render_final_report_v3_discord_is_user_facing_and_short() -> None:
    decision = parse_final_report_decision(json.dumps(_valid_payload(), ensure_ascii=False))
    validate_final_report_decision(decision, source_text="동의 쿠폰 법무 리스크")

    content = render_final_report_v3_discord(decision, agenda="외계인 DJ 쿠폰 회의")

    assert len(content) <= 1600
    assert "# 📋 최종보고서" in content
    assert "## 🎯 결론" in content
    assert "## ✅ 합의안" in content
    assert "## 🚀 다음 액션" in content
    assert "## ⚠️ 리스크" in content
    assert "## 🔍 검증" in content
    assert "model evidence" not in content
    assert "deepseek" not in content.lower()
    assert "runtime artifact" not in content


def test_render_final_report_v3_local_contains_full_trace() -> None:
    decision = parse_final_report_decision(json.dumps(_valid_payload(), ensure_ascii=False))

    content = render_final_report_v3_local(
        decision,
        agenda="외계인 DJ 쿠폰 회의",
        source_text="동의 쿠폰 법무 리스크",
        model_evidence="validator=PASS; model=rtk-rewrite",
    )

    assert "# Final Report v3" in content
    assert "## Source Summary" in content
    assert "## Model Evidence" in content
    assert "validator=PASS" in content


def test_on_demand_final_report_generates_v3_artifacts_only_when_requested(
    tmp_path: Path,
) -> None:
    result = run_phase14_multi_bot_pilot(
        root=tmp_path,
        mode="dry-run",
        trigger_text="음성 사연 동의와 쿠폰 중복 사용, 법무 약관 리스크를 검토하는 회의",
    )
    assert result.ok

    run_dir = tmp_path / "runtime" / "meeting_runs" / result.meeting_run.meeting_run_id
    assert not (run_dir / "final_report_v3.md").exists()
    assert not (run_dir / "decision_summary.json").exists()

    export = run_on_demand_export(
        tmp_path,
        result.meeting_run.meeting_run_id,
        OnDemandExportType.FINAL_REPORT,
    )

    assert export.ok
    assert len(export.content) <= 1600
    assert "# 📋 최종보고서" in export.content
    assert (run_dir / "final_report_v3.md").exists()
    assert (run_dir / "decision_summary.json").exists()

    decision_payload = json.loads((run_dir / "decision_summary.json").read_text(encoding="utf-8"))
    assert "동의" in json.dumps(decision_payload, ensure_ascii=False)
    assert "쿠폰" in json.dumps(decision_payload, ensure_ascii=False)
