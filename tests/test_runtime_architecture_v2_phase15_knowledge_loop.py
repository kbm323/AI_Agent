from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from src.runtime_architecture_v2.knowledge import (
    KnowledgeEntry,
    retrieve_knowledge_context,
    run_phase15_knowledge_loop_pilot,
    sanitize_knowledge_text,
    write_meeting_knowledge,
)
from src.runtime_architecture_v2.multi_bot import MultiBotSession
from src.runtime_architecture_v2.schemas import MeetingRun, MeetingRunState


def _meeting_run() -> MeetingRun:
    return MeetingRun.create(
        meeting_run_id="mr_phase15_test",
        trigger_text="신규 버추얼 아이돌 데뷔 컨셉 회의",
        user_id="u1",
        channel_id="c1",
        thread_id="t1",
    )


def _session() -> MultiBotSession:
    return MultiBotSession(
        meeting_run_id="mr_phase15_test",
        participants=("content_lead", "marketing_lead", "quality_lead"),
        rounds=(),
        consensus_reached=True,
        escalation_required=False,
        consensus_summary=(
            "Luna 데뷔는 팬 참여형 쇼츠와 세계관 티저를 결합한다. "
            "api_key=LEAK123456 @everyone Bearer SHOULD_NOT_LEAK"
        ),
    )


def test_public_sanitizer_redacts_secret_and_everyone():
    assert sanitize_knowledge_text("token=abc123 @everyone") == (
        "[REDACTED_SECRET] @[redacted-mention]"
    )


def test_knowledge_entry_serializes_with_obsidian_compatible_frontmatter():
    entry = KnowledgeEntry(
        knowledge_id="kb_mr_phase15_test_summary",
        title="Phase 15 Meeting Summary",
        kind="meeting_summary",
        source_meeting_run_id="mr_phase15_test",
        summary="팬 참여형 쇼츠 전략",
        tags=("phase15", "ai-company"),
        links=("[[meetings/mr_phase15_test]]",),
    )

    payload = entry.to_dict()
    markdown = entry.to_markdown()

    assert payload["knowledge_id"] == "kb_mr_phase15_test_summary"
    assert "obsidian_compatible: true" in markdown
    assert "source_meeting_run_id: mr_phase15_test" in markdown
    assert "[[meetings/mr_phase15_test]]" in markdown


def test_write_meeting_knowledge_creates_raw_wiki_index_and_log(tmp_path: Path):
    result = write_meeting_knowledge(
        root=tmp_path,
        meeting_run=_meeting_run(),
        session=_session(),
        phase="phase15",
    )

    assert result.ok is True
    assert result.raw_path.exists()
    assert result.wiki_path.exists()
    assert result.index_path.exists()
    assert result.log_path.exists()
    assert result.agents_path.exists()
    assert result.entry.source_meeting_run_id == "mr_phase15_test"
    assert "knowledge/wiki/meetings" in Path(
        result.meeting_run.metadata["knowledge_refs"][0]
    ).as_posix()

    raw = result.raw_path.read_text(encoding="utf-8")
    wiki = result.wiki_path.read_text(encoding="utf-8")
    index = result.index_path.read_text(encoding="utf-8")
    log = result.log_path.read_text(encoding="utf-8")

    assert "Luna 데뷔" in raw
    assert "팬 참여형 쇼츠" in wiki
    assert "[[meetings/mr_phase15_test]]" in index
    assert "kb_mr_phase15_test_meeting_summary" in log


def test_knowledge_writer_redacts_secrets_and_uncontrolled_mentions(tmp_path: Path):
    session = _session()
    result = write_meeting_knowledge(
        root=tmp_path,
        meeting_run=_meeting_run(),
        session=session,
        phase="phase15",
    )

    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            result.raw_path,
            result.wiki_path,
            result.index_path,
            result.log_path,
        )
    )

    assert "LEAK123456" not in combined
    assert "SHOULD_NOT_LEAK" not in combined
    assert "@everyone" not in combined
    assert "@here" not in combined
    assert "[REDACTED" in combined
    assert result.entry.summary == sanitize_knowledge_text(session.consensus_summary)


def test_knowledge_writer_redacts_mixed_case_mentions_and_bearer_values(
    tmp_path: Path,
):
    session = MultiBotSession(
        meeting_run_id="mr_phase15_test",
        participants=("content_lead",),
        rounds=(),
        consensus_reached=True,
        escalation_required=False,
        consensus_summary=(
            "@Everyone launch note\n"
            "Token: SHOULD_NOT_LEAK_EITHER\n"
            "Bearer ABCDEFG1234567"
        ),
    )

    result = write_meeting_knowledge(
        root=tmp_path,
        meeting_run=_meeting_run(),
        session=session,
        phase="phase15",
    )

    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            result.raw_path,
            result.wiki_path,
            result.index_path,
            result.log_path,
        )
    )
    assert "@Everyone" not in combined
    assert "SHOULD_NOT_LEAK_EITHER" not in combined
    assert "ABCDEFG1234567" not in combined
    assert "[REDACTED_SECRET]" in combined


def test_knowledge_writer_redacts_participant_and_metadata_fields(tmp_path: Path):
    session = MultiBotSession(
        meeting_run_id="mr_phase15_test",
        participants=("@everyone", "password=PARTICIPANTSECRET"),
        rounds=(),
        consensus_reached=True,
        escalation_required=False,
        consensus_summary="safe summary",
    )

    result = write_meeting_knowledge(
        root=tmp_path,
        meeting_run=_meeting_run(),
        session=session,
        phase="phase15",
    )

    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            result.raw_path,
            result.wiki_path,
            result.index_path,
            result.log_path,
        )
    )
    assert "@everyone" not in combined
    assert "PARTICIPANTSECRET" not in combined
    assert "@[redacted-mention]" in combined
    assert "[REDACTED_SECRET]" in combined


def test_retrieve_knowledge_context_returns_relevant_wiki_notes(tmp_path: Path):
    write_meeting_knowledge(
        root=tmp_path,
        meeting_run=_meeting_run(),
        session=_session(),
        phase="phase15",
    )

    context = retrieve_knowledge_context(
        root=tmp_path,
        query="Luna 팬 참여 쇼츠 데뷔",
        limit=2,
    )

    assert context.ok is True
    assert context.query == "Luna 팬 참여 쇼츠 데뷔"
    assert len(context.matches) == 1
    assert context.matches[0]["knowledge_id"] == "kb_mr_phase15_test_meeting_summary"
    assert "팬 참여형 쇼츠" in context.context_markdown


def test_retrieve_knowledge_context_is_stable_for_ties_and_no_matches(
    tmp_path: Path,
):
    first = _meeting_run()
    second = MeetingRun.create(
        meeting_run_id="mr_phase15_test_b",
        trigger_text="신규 버추얼 아이돌 데뷔 컨셉 회의",
        user_id="u1",
        channel_id="c1",
        thread_id="t1",
    )
    write_meeting_knowledge(
        root=tmp_path,
        meeting_run=first,
        session=_session(),
        phase="phase15",
    )
    write_meeting_knowledge(
        root=tmp_path,
        meeting_run=second,
        session=MultiBotSession(
            meeting_run_id="mr_phase15_test_b",
            participants=("content_lead",),
            rounds=(),
            consensus_reached=True,
            escalation_required=False,
            consensus_summary=(
                "Luna 데뷔는 팬 참여형 쇼츠와 세계관 티저를 결합한다."
            ),
        ),
        phase="phase15",
    )

    context = retrieve_knowledge_context(
        root=tmp_path, query="Luna 팬 참여 쇼츠", limit=2
    )
    none = retrieve_knowledge_context(
        root=tmp_path, query="unmatched-zebra", limit=2
    )

    assert [m["path"] for m in context.matches] == sorted(
        m["path"] for m in context.matches
    )
    assert none.ok is True
    assert none.matches == ()
    assert none.context_markdown == ""


def test_knowledge_writer_rejects_unsafe_meeting_run_id(tmp_path: Path):
    unsafe = MeetingRun(
        meeting_run_id="../escape",
        state=MeetingRunState.COMPLETED,
        trigger={"text": "bad"},
    )

    try:
        write_meeting_knowledge(
            root=tmp_path,
            meeting_run=unsafe,
            session=MultiBotSession(
                meeting_run_id="../escape",
                participants=(),
                rounds=(),
                consensus_reached=False,
                escalation_required=True,
            ),
            phase="phase15",
        )
    except ValueError as exc:
        assert "unsafe meeting_run_id" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("unsafe meeting_run_id must be rejected")


def test_knowledge_writer_rejects_dot_only_meeting_run_ids(tmp_path: Path):
    for unsafe_id in (".", "..", ".hidden"):
        unsafe = MeetingRun(
            meeting_run_id=unsafe_id,
            state=MeetingRunState.COMPLETED,
            trigger={"text": "bad"},
        )

        try:
            write_meeting_knowledge(
                root=tmp_path,
                meeting_run=unsafe,
                session=MultiBotSession(
                    meeting_run_id=unsafe_id,
                    participants=(),
                    rounds=(),
                    consensus_reached=False,
                    escalation_required=True,
                ),
                phase="phase15",
            )
        except ValueError as exc:
            assert "unsafe meeting_run_id" in str(exc)
        else:  # pragma: no cover
            raise AssertionError(
                f"unsafe meeting_run_id must be rejected: {unsafe_id}"
            )


def test_phase15_dry_run_pilot_writes_and_retrieves_knowledge(tmp_path: Path):
    result = run_phase15_knowledge_loop_pilot(root=tmp_path, mode="dry-run")

    assert result["ok"] is True
    assert result["mode"] == "dry-run"
    assert result["knowledge_entry_id"].startswith("kb_")
    assert result["retrieval_match_count"] >= 1
    assert Path(result["raw_path"]).exists()
    assert Path(result["wiki_path"]).exists()


def test_phase15_cli_dry_run_outputs_machine_readable_json(tmp_path: Path):
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/run_phase15_knowledge_loop_pilot.py",
            "--mode",
            "dry-run",
            "--root",
            str(tmp_path),
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True
    assert payload["mode"] == "dry-run"
    assert payload["retrieval_match_count"] >= 1
    assert completed.stderr == ""
