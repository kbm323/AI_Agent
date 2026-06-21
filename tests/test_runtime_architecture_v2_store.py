from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.runtime_architecture_v2.schemas import (
    MeetingRun,
    MeetingRunState,
    RecoveryCheckpoint,
)
from src.runtime_architecture_v2.store import MeetingRunStore, StoreError


def _meeting_run(meeting_run_id: str = "mr_001") -> MeetingRun:
    return MeetingRun.create(
        meeting_run_id=meeting_run_id,
        trigger_text="기술 회의 열어줘",
        user_id="user-1",
        channel_id="channel-1",
        thread_id="thread-1",
        guild_id="guild-1",
        hermes_session_id="sess-1",
        priority="P1",
    )


def test_store_saves_meeting_run_with_expected_layout_and_deterministic_json(
    tmp_path: Path,
):
    store = MeetingRunStore(tmp_path)
    run = _meeting_run()

    path = store.save_meeting_run(run)

    assert path == tmp_path / "runtime" / "meeting_runs" / "mr_001" / "meeting_run.json"
    assert path.exists()
    assert (path.parent / "packets").is_dir()
    assert (path.parent / "worker_outputs").is_dir()
    assert (path.parent / "validation").is_dir()
    assert (path.parent / "discord_projection").is_dir()
    assert (path.parent / "checkpoints").is_dir()
    assert (path.parent / "final_report.md").exists() is False

    raw = path.read_text(encoding="utf-8")
    assert raw.endswith("\n")
    assert json.loads(raw) == run.to_dict()
    assert store.load_meeting_run("mr_001") == run


def test_store_rejects_path_traversal_meeting_run_ids(tmp_path: Path):
    store = MeetingRunStore(tmp_path)

    with pytest.raises(StoreError, match="invalid meeting_run_id"):
        store.save_meeting_run(_meeting_run("../escape"))

    with pytest.raises(StoreError, match="invalid meeting_run_id"):
        store.load_meeting_run("mr/escape")


def test_store_reports_missing_and_corrupt_meeting_run_as_structured_errors(
    tmp_path: Path,
):
    store = MeetingRunStore(tmp_path)

    with pytest.raises(StoreError) as missing:
        store.load_meeting_run("mr_missing")
    assert missing.value.code == "missing_meeting_run"
    assert missing.value.meeting_run_id == "mr_missing"

    run_dir = tmp_path / "runtime" / "meeting_runs" / "mr_bad"
    run_dir.mkdir(parents=True)
    (run_dir / "meeting_run.json").write_text("{not json", encoding="utf-8")

    with pytest.raises(StoreError) as corrupt:
        store.load_meeting_run("mr_bad")
    assert corrupt.value.code == "corrupt_meeting_run"
    assert corrupt.value.meeting_run_id == "mr_bad"


def test_checkpoint_round_trip_latest_and_missing_default(tmp_path: Path):
    store = MeetingRunStore(tmp_path)
    store.save_meeting_run(_meeting_run())
    first = RecoveryCheckpoint(
        checkpoint_id="chk_001",
        meeting_run_id="mr_001",
        state=MeetingRunState.ACTIVE,
        completed_worker_task_ids=("wt_001",),
        pending_worker_task_ids=("wt_002",),
        idempotency_key="mr_001:active:wt_001",
        replay_token="replay-001",
    )
    second = RecoveryCheckpoint(
        checkpoint_id="chk_002",
        meeting_run_id="mr_001",
        state=MeetingRunState.VALIDATING,
        completed_worker_task_ids=("wt_001", "wt_002"),
        idempotency_key="mr_001:validating:wt_002",
        replay_token="replay-002",
    )

    first_path = store.save_checkpoint(first)
    second_path = store.save_checkpoint(second)

    assert first_path.name == "chk_001.json"
    assert second_path.name == "chk_002.json"
    assert json.loads(second_path.read_text(encoding="utf-8"))[
        "checkpoint_path"
    ] == str(second_path)
    assert store.load_checkpoint("mr_001", "chk_001").checkpoint_id == "chk_001"
    assert store.load_latest_checkpoint("mr_001") == RecoveryCheckpoint.from_dict(
        json.loads(second_path.read_text(encoding="utf-8"))
    )

    missing_default = store.load_latest_checkpoint("mr_no_checkpoint")
    assert missing_default.meeting_run_id == "mr_no_checkpoint"
    assert missing_default.checkpoint_id == ""
    assert missing_default.state == MeetingRunState.CREATED

    with pytest.raises(StoreError, match="invalid checkpoint_id"):
        store.save_checkpoint(
            RecoveryCheckpoint(
                checkpoint_id="../escape",
                meeting_run_id="mr_001",
                state=MeetingRunState.ACTIVE,
            )
        )


def test_store_layout_does_not_create_queue_db_or_copy_hermes_state(tmp_path: Path):
    store = MeetingRunStore(tmp_path)
    run = _meeting_run()

    store.save_meeting_run(run)
    run_dir = tmp_path / "runtime" / "meeting_runs" / "mr_001"
    persisted = json.loads((run_dir / "meeting_run.json").read_text(encoding="utf-8"))

    assert not (run_dir / "queue.db").exists()
    assert not (run_dir / "hermes_state.json").exists()
    assert not (run_dir / "discord_history.json").exists()
    assert "queue_db" not in persisted
    assert "openclaw" not in json.dumps(persisted).lower()
    assert "hermes_memory" not in persisted
    assert "session_id" in persisted["hermes_refs"]


def test_corrupt_checkpoint_is_reported_as_structured_error(tmp_path: Path):
    store = MeetingRunStore(tmp_path)
    store.save_meeting_run(_meeting_run())
    checkpoint_path = (
        tmp_path
        / "runtime"
        / "meeting_runs"
        / "mr_001"
        / "checkpoints"
        / "chk_bad.json"
    )
    checkpoint_path.write_text("{not json", encoding="utf-8")

    with pytest.raises(StoreError) as corrupt:
        store.load_checkpoint("mr_001", "chk_bad")
    assert corrupt.value.code == "corrupt_checkpoint"
    assert corrupt.value.meeting_run_id == "mr_001"


def test_append_jsonl_decision_and_audit_events_include_meeting_run_id(tmp_path: Path):
    store = MeetingRunStore(tmp_path)
    store.save_meeting_run(_meeting_run())

    decision_path = store.append_decision_event(
        "mr_001",
        {
            "decision": "route_to_tech",
            "reason": "technical execution",
            "meeting_run_id": "wrong",
        },
    )
    audit_path = store.append_audit_event(
        "mr_001",
        {"action": "validator_passed", "validator": "glm_validator"},
    )

    assert decision_path.name == "decision_log.jsonl"
    assert audit_path.name == "audit_log.jsonl"
    decision_event = json.loads(
        decision_path.read_text(encoding="utf-8").splitlines()[0]
    )
    audit_event = json.loads(audit_path.read_text(encoding="utf-8").splitlines()[0])
    assert decision_event["meeting_run_id"] == "mr_001"
    assert decision_event["decision"] == "route_to_tech"
    assert "logged_at" in decision_event
    assert audit_event["meeting_run_id"] == "mr_001"
    assert audit_event["action"] == "validator_passed"
    assert "logged_at" in audit_event
