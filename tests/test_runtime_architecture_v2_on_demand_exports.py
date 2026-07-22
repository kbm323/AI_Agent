"""Tests for on-demand meeting artifact exports (Phase 32 / Phase 5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.runtime_architecture_v2.multi_bot import (
    BotMessage,
    MeetingRound,
    MultiBotSession,
    run_phase14_multi_bot_pilot,
)
from src.runtime_architecture_v2.on_demand_exports import (
    OnDemandExportType,
    find_latest_meeting_run_id,
    run_on_demand_export,
)
from src.runtime_architecture_v2.schemas import MeetingOutcome, MeetingRun
from src.runtime_architecture_v2.store import MeetingRunStore


def _persist_canonical_meeting(root: Path) -> str:
    meeting_run_id = "mr_canonical_report"
    store = MeetingRunStore(root)
    store.save_meeting_run(
        MeetingRun.create(
            meeting_run_id=meeting_run_id,
            trigger_text="정식 근거 회의",
            user_id="user-1",
            channel_id="channel-1",
            thread_id="thread-1",
        )
    )
    session = MultiBotSession(
        meeting_run_id=meeting_run_id,
        participants=("content_lead", "validation_audit"),
        rounds=(
            MeetingRound(
                round_number=1,
                phase="opinions",
                messages=(
                    BotMessage(
                        bot_role="content_lead",
                        meeting_run_id=meeting_run_id,
                        round=1,
                        msg_type="opinion",
                        content="고유 콘텐츠 근거: 오로라 콘셉트를 채택합니다.",
                        generation_status="live",
                        provider="opencode-go",
                        model="qwen3.7-plus",
                    ),
                ),
            ),
            MeetingRound(
                round_number=2,
                phase="rebuttals",
                messages=(
                    BotMessage(
                        bot_role="validation_audit",
                        meeting_run_id=meeting_run_id,
                        round=2,
                        msg_type="rebuttal",
                        content="고유 검증 근거: 출시 전 권리 검토가 필요합니다.",
                        generation_status="live",
                        provider="opencode-go",
                        model="glm-5.1",
                    ),
                ),
            ),
        ),
        consensus_reached=False,
        escalation_required=True,
        consensus_summary="오로라 콘셉트에는 합의했지만 권리 검토가 남았습니다.",
    )
    outcome = MeetingOutcome(
        meeting_run_id=meeting_run_id,
        status="partial_agreement",
        summary="오로라 콘셉트에는 합의했지만 권리 검토가 남았습니다.",
        agreements=("오로라 콘셉트 채택",),
        disagreements=("권리 검토 완료 시점",),
        action_items=("법무 담당자가 권리 검토표를 작성한다",),
        evidence_refs=(
            "round:1:content_lead",
            "round:2:validation_audit",
        ),
        validator_notes=("출시 전 재검증",),
        generation_status="live",
        model="glm-5.1",
    )
    store.save_meeting_session(session)
    store.save_meeting_outcome(outcome)
    return meeting_run_id


def test_on_demand_export_type_enum_covers_all_request_types() -> None:
    """Every user-facing request must map to an export type."""
    assert OnDemandExportType.SUMMARY == "summary"
    assert OnDemandExportType.FINAL_REPORT == "final_report"
    assert OnDemandExportType.AGREEMENT == "agreement"
    assert OnDemandExportType.ACTION_ITEMS == "action_items"


def test_find_latest_meeting_run_id_returns_none_for_empty_root(tmp_path: Path) -> None:
    """Empty root returns None because no meeting runs exist."""
    assert find_latest_meeting_run_id(tmp_path) is None


def test_find_latest_meeting_run_id_returns_id_after_dry_run(
    tmp_path: Path,
) -> None:
    """After a dry-run meeting, the latest meeting_run_id is found."""
    result = run_phase14_multi_bot_pilot(root=tmp_path, mode="dry-run")
    assert result.ok

    run_id = find_latest_meeting_run_id(tmp_path)
    assert run_id is not None
    assert run_id == result.meeting_run.meeting_run_id


def test_run_on_demand_export_summary(tmp_path: Path) -> None:
    """Requesting 'summary' export returns a short meeting summary."""
    result = run_phase14_multi_bot_pilot(
        root=tmp_path,
        mode="dry-run",
        trigger_text="신규 버추얼 아이돌 데뷔 컨셉 회의",
    )
    assert result.ok

    export = run_on_demand_export(
        tmp_path,
        result.meeting_run.meeting_run_id,
        OnDemandExportType.SUMMARY,
    )
    assert export.ok
    assert export.export_type == "summary"
    assert "회의 요약" in export.content
    assert "신규 버추얼 아이돌 데뷔 컨셉 회의" in export.content


def test_run_on_demand_export_final_report(tmp_path: Path) -> None:
    """Requesting 'final_report' export uses _build_final_report()."""
    result = run_phase14_multi_bot_pilot(
        root=tmp_path,
        mode="dry-run",
        trigger_text="법무 계약 검토 회의",
        live_bot_roles_override=(),
        fake_bot_roles_override=(
            "ceo_coordinator",
            "content_lead",
            "art_lead",
            "tech_lead",
            "marketing_lead",
            "validation_audit",
        ),
    )
    assert result.ok

    export = run_on_demand_export(
        tmp_path,
        result.meeting_run.meeting_run_id,
        OnDemandExportType.FINAL_REPORT,
    )
    assert export.ok
    assert "# 📋" in export.content
    assert "## 🎯 결론" in export.content
    assert "## ✅ 합의안" in export.content


def test_run_on_demand_export_agreement(tmp_path: Path) -> None:
    """Requesting 'agreement' export returns agreement-focused document."""
    result = run_phase14_multi_bot_pilot(
        root=tmp_path, mode="dry-run", trigger_text="합의 테스트 회의"
    )
    assert result.ok

    export = run_on_demand_export(
        tmp_path,
        result.meeting_run.meeting_run_id,
        OnDemandExportType.AGREEMENT,
    )
    assert export.ok
    assert "합의서" in export.content
    assert "합의안" in export.content
    assert "합의 테스트 회의" in export.content


def test_run_on_demand_export_action_items(tmp_path: Path) -> None:
    """Requesting 'action_items' export returns action items document."""
    result = run_phase14_multi_bot_pilot(
        root=tmp_path, mode="dry-run", trigger_text="할 일 정리 회의"
    )
    assert result.ok

    export = run_on_demand_export(
        tmp_path,
        result.meeting_run.meeting_run_id,
        OnDemandExportType.ACTION_ITEMS,
    )
    assert export.ok
    assert "다음 할 일" in export.content
    assert "작업 항목" in export.content
    assert "할 일 정리 회의" in export.content


@pytest.mark.parametrize(
    "export_type",
    (
        OnDemandExportType.SUMMARY,
        OnDemandExportType.FINAL_REPORT,
        OnDemandExportType.AGREEMENT,
        OnDemandExportType.ACTION_ITEMS,
    ),
)
def test_exports_use_canonical_session_and_outcome_evidence(
    tmp_path: Path,
    export_type: OnDemandExportType,
) -> None:
    meeting_run_id = _persist_canonical_meeting(tmp_path)

    export = run_on_demand_export(tmp_path, meeting_run_id, export_type)

    assert export.ok is True
    assert "오로라" in export.content
    assert "회의 결과를 검토하고 후속 작업을 확정한다" not in export.content
    report_path = (
        tmp_path
        / "runtime"
        / "meeting_runs"
        / meeting_run_id
        / "reports"
        / f"{export_type}.md"
    )
    assert report_path.read_text(encoding="utf-8") == export.content


def test_action_export_uses_structured_action_items(tmp_path: Path) -> None:
    meeting_run_id = _persist_canonical_meeting(tmp_path)

    export = run_on_demand_export(
        tmp_path,
        meeting_run_id,
        OnDemandExportType.ACTION_ITEMS,
    )

    assert "법무 담당자가 권리 검토표를 작성한다" in export.content
    assert "실행 계획을 수립한다" not in export.content


def test_legacy_export_discloses_missing_canonical_evidence(tmp_path: Path) -> None:
    store = MeetingRunStore(tmp_path)
    run = MeetingRun.create(
        meeting_run_id="mr_legacy_report",
        trigger_text="과거 회의",
        user_id="user-1",
        channel_id="channel-1",
        thread_id="thread-1",
    )
    store.save_meeting_run(run)

    export = run_on_demand_export(
        tmp_path,
        run.meeting_run_id,
        OnDemandExportType.AGREEMENT,
    )

    assert export.ok is True
    assert "레거시" in export.content
    assert "검증 가능한 합의 없음" in export.content


def test_run_on_demand_export_unknown_type_fails(tmp_path: Path) -> None:
    """Unknown export type returns not-ok result."""
    result = run_phase14_multi_bot_pilot(root=tmp_path, mode="dry-run")
    assert result.ok

    export = run_on_demand_export(
        tmp_path,
        result.meeting_run.meeting_run_id,
        "unknown_type",  # type: ignore[arg-type]
    )
    assert export.ok is False
    assert "unknown export type" in export.error


def test_on_demand_export_does_not_trigger_on_default_meeting(
    tmp_path: Path,
) -> None:
    """Default meeting produces no automatic report; exports must be explicit."""
    result = run_phase14_multi_bot_pilot(root=tmp_path, mode="dry-run")
    assert result.ok
    # Default meeting does NOT auto-generate final_report
    assert result.final_report == ""
