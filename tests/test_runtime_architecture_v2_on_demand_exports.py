"""Tests for on-demand meeting artifact exports (Phase 32 / Phase 5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.runtime_architecture_v2.on_demand_exports import (
    OnDemandExportResult,
    OnDemandExportType,
    find_latest_meeting_run_id,
    run_on_demand_export,
)
from src.runtime_architecture_v2.multi_bot import run_phase14_multi_bot_pilot


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
