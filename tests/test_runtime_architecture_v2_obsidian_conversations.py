from __future__ import annotations

import json
import multiprocessing
import os
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from src.runtime_architecture_v2 import obsidian_conversations as module
from src.runtime_architecture_v2.conversation_summary import (
    ActionItem,
    ConversationSummary,
)
from src.runtime_architecture_v2.discord_conversation import (
    BotIdentity,
    DiscordAttachment,
    DiscordAuthor,
    DiscordConversation,
    DiscordMessage,
    ParticipantResolver,
)
from src.runtime_architecture_v2.obsidian_conversations import (
    ObsidianConversationStore,
)
from src.runtime_architecture_v2.schemas import MeetingRun


def _conversation(
    latest_id: str = "2", content: str = "Start in 3 days as decided"
) -> DiscordConversation:
    return DiscordConversation(
        guild_id="1",
        parent_channel_id="100",
        thread_id="200",
        thread_name="Content plan",
        visibility="guild",
        messages=(
            DiscordMessage(
                message_id="1",
                created_at="2026-07-13T01:00:00+00:00",
                content="Monday planning with participants",
                author=DiscordAuthor("300", "KBM"),
            ),
            DiscordMessage(
                message_id=latest_id,
                created_at="2026-07-13T01:05:00+00:00",
                content=content,
                author=DiscordAuthor("400", "Content Lead", bot=True),
            ),
        ),
    )


def _summary(important: bool = True) -> ConversationSummary:
    return ConversationSummary(
        summary="The team agreed on a three-day launch direction.",
        decisions=("Start in 3 days",) if important else (),
        action_items=(ActionItem("Draft the outline", "Content Lead"),)
        if important
        else (),
    )


def _meeting() -> MeetingRun:
    return MeetingRun.create(
        meeting_run_id="mr-1",
        trigger_text="Content plan",
        user_id="300",
        channel_id="100",
        thread_id="200",
    )


def _multiprocess_save_worker(
    *,
    label,
    vault_root,
    runtime_root,
    thread_id,
    latest_id,
    start_event,
    result_queue,
    raw_started=None,
    release_raw=None,
    delay_shared_writes=False,
    save_ready=None,
):
    if raw_started is not None and release_raw is not None:
        original_write_raw = module.ObsidianConversationStore._write_raw_exclusive

        def gated_write_raw(self, path, markdown):
            raw_started.set()
            if not release_raw.wait(timeout=15):
                raise TimeoutError("test_raw_release_timeout")
            return original_write_raw(self, path, markdown)

        module.ObsidianConversationStore._write_raw_exclusive = gated_write_raw

    if delay_shared_writes:
        original_atomic_write = module._atomic_write_text

        def delayed_atomic_write(path, text):
            if path.name in {"log.md", "index.md"}:
                time.sleep(0.08)
            return original_atomic_write(path, text)

        module._atomic_write_text = delayed_atomic_write

    if not start_event.wait(timeout=15):
        result_queue.put((label, "error", "start_timeout"))
        return
    if save_ready is not None:
        save_ready.set()
    try:
        result = ObsidianConversationStore(
            vault_root=vault_root,
            runtime_root=runtime_root,
        ).save(
            conversation=replace(
                _conversation(latest_id),
                thread_id=thread_id,
                thread_name=f"Thread {thread_id}",
            ),
            participant_resolver=ParticipantResolver({}),
            summary=_summary(),
        )
    except Exception as exc:
        result_queue.put((label, "error", f"{type(exc).__name__}: {exc}"))
    else:
        result_queue.put((label, "ok", result.status))


def _multiprocess_lock_holder(lock_path, ready, release):
    with module._InterProcessFileLock(Path(lock_path)):
        ready.set()
        if not release.wait(timeout=15):
            raise TimeoutError("test_lock_release_timeout")


def _finish_processes(processes):
    for process in processes:
        process.join(timeout=20)
    hanging = [process for process in processes if process.is_alive()]
    for process in hanging:
        process.terminate()
        process.join(timeout=5)
    assert not hanging, "multiprocessing regression timed out"
    assert all(process.exitcode == 0 for process in processes)


def test_first_save_creates_snapshot_canonical_checkpoint_log_and_meeting_index(
    tmp_path,
):
    vault = tmp_path / "vault"
    store = ObsidianConversationStore(vault_root=vault, runtime_root=tmp_path)
    result = store.save(
        conversation=_conversation(),
        participant_resolver=ParticipantResolver({}),
        summary=_summary(),
        meeting_run=_meeting(),
    )

    assert result.status == "created"
    assert result.classification == "meeting"
    assert result.new_message_count == 2
    assert (vault / result.snapshot_path).exists()
    assert (vault / result.canonical_path).exists()
    assert (vault / "wiki" / "log.md").exists()
    assert "mr-1" in (vault / "wiki" / "index.md").read_text(encoding="utf-8")
    checkpoint = json.loads(
        (tmp_path / "runtime" / "discord_save" / "200.json").read_text(encoding="utf-8")
    )
    assert checkpoint == {
        "thread_id": "200",
        "latest_message_id": "2",
        "canonical_path": result.canonical_path,
        "snapshot_paths": [result.snapshot_path],
    }


def test_same_latest_message_id_is_no_change_and_creates_no_second_snapshot(
    tmp_path,
):
    vault = tmp_path / "vault"
    store = ObsidianConversationStore(vault_root=vault, runtime_root=tmp_path)
    kwargs = {
        "conversation": _conversation(),
        "participant_resolver": ParticipantResolver({}),
        "summary": _summary(),
        "meeting_run": None,
    }

    first = store.save(**kwargs)
    second = store.save(**kwargs)

    assert first.status == "created"
    assert second.status == "unchanged"
    assert second.new_message_count == 0
    assert len(list((vault / "raw" / "chat-logs").glob("*.md"))) == 1
    log = (vault / "wiki" / "log.md").read_text(encoding="utf-8")
    assert log.count("200:2") == 1


def test_same_latest_conversation_can_acquire_meeting_linkage_without_new_snapshot(
    tmp_path,
):
    vault = tmp_path / "vault"
    store = ObsidianConversationStore(vault_root=vault, runtime_root=tmp_path)
    first = store.save(
        conversation=_conversation(),
        participant_resolver=ParticipantResolver({}),
        summary=_summary(),
        meeting_run=None,
    )
    snapshot = vault / first.snapshot_path
    original_snapshot = snapshot.read_bytes()

    linked = store.save(
        conversation=_conversation(),
        participant_resolver=ParticipantResolver({}),
        summary=_summary(),
        meeting_run=_meeting(),
    )

    raw_paths = list((vault / "raw" / "chat-logs").glob("*.md"))
    canonical = (vault / linked.canonical_path).read_text(encoding="utf-8")
    index = (vault / "wiki" / "index.md").read_text(encoding="utf-8")
    assert linked.status == "unchanged"
    assert linked.classification == "meeting"
    assert raw_paths == [snapshot]
    assert snapshot.read_bytes() == original_snapshot
    assert 'type: "conversation"' in snapshot.read_text(encoding="utf-8")
    assert 'meeting_run_id: ""' in snapshot.read_text(encoding="utf-8")
    assert 'type: "meeting"' in canonical
    assert 'meeting_run_id: "mr-1"' in canonical
    assert "meeting=mr-1" in index


def test_same_latest_meeting_transition_rejects_transcript_mutation(tmp_path):
    vault = tmp_path / "vault"
    store = ObsidianConversationStore(vault_root=vault, runtime_root=tmp_path)
    first = store.save(
        conversation=_conversation(),
        participant_resolver=ParticipantResolver({}),
        summary=_summary(),
        meeting_run=None,
    )
    snapshot = vault / first.snapshot_path
    original_snapshot = snapshot.read_bytes()
    original_canonical = (vault / first.canonical_path).read_bytes()

    with pytest.raises(ValueError, match="invalid_immutable_snapshot"):
        store.save(
            conversation=_conversation(content="Mutated same-range transcript"),
            participant_resolver=ParticipantResolver({}),
            summary=_summary(),
            meeting_run=_meeting(),
        )

    assert snapshot.read_bytes() == original_snapshot
    assert (vault / first.canonical_path).read_bytes() == original_canonical
    assert len(list((vault / "raw" / "chat-logs").glob("*.md"))) == 1


def test_new_message_creates_new_snapshot_and_updates_same_canonical_page(tmp_path):
    vault = tmp_path / "vault"
    store = ObsidianConversationStore(vault_root=vault, runtime_root=tmp_path)
    resolver = ParticipantResolver({})
    first = store.save(
        conversation=_conversation(),
        participant_resolver=resolver,
        summary=_summary(),
        meeting_run=None,
    )
    second = store.save(
        conversation=_conversation("3", "The outline is drafted"),
        participant_resolver=resolver,
        summary=_summary(),
        meeting_run=None,
    )

    assert second.status == "updated"
    assert second.new_message_count == 1
    assert second.canonical_path == first.canonical_path
    assert second.snapshot_path != first.snapshot_path
    assert len(list((vault / "raw" / "chat-logs").glob("*.md"))) == 2
    canonical = (vault / second.canonical_path).read_text(encoding="utf-8")
    assert first.snapshot_path in canonical
    assert second.snapshot_path in canonical


def test_raw_snapshot_is_never_overwritten(tmp_path):
    vault = tmp_path / "vault"
    store = ObsidianConversationStore(vault_root=vault, runtime_root=tmp_path)
    result = store.save(
        conversation=_conversation(),
        participant_resolver=ParticipantResolver({}),
        summary=_summary(),
        meeting_run=None,
    )
    snapshot = vault / result.snapshot_path
    original = snapshot.read_text(encoding="utf-8")

    store.save(
        conversation=_conversation(),
        participant_resolver=ParticipantResolver({}),
        summary=replace(_summary(), summary="Changed summary"),
        meeting_run=None,
    )

    assert snapshot.read_text(encoding="utf-8") == original


def test_ordinary_unimportant_conversation_updates_log_but_not_index(tmp_path):
    vault = tmp_path / "vault"
    result = ObsidianConversationStore(vault_root=vault, runtime_root=tmp_path).save(
        conversation=_conversation(),
        participant_resolver=ParticipantResolver({}),
        summary=_summary(important=False),
        meeting_run=None,
    )

    assert result.status == "created"
    assert (vault / "wiki" / "log.md").exists()
    assert not (vault / "wiki" / "index.md").exists()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("guild_id", "guild-1"),
        ("parent_channel_id", "../outside"),
        ("thread_id", "../outside"),
    ],
)
def test_paths_reject_non_numeric_discord_container_ids(tmp_path, field, value):
    store = ObsidianConversationStore(
        vault_root=tmp_path / "vault", runtime_root=tmp_path
    )
    unsafe = replace(_conversation(), **{field: value}, thread_name="../../outside")

    with pytest.raises(ValueError, match=field):
        store.save(
            conversation=unsafe,
            participant_resolver=ParticipantResolver({}),
            summary=_summary(),
            meeting_run=None,
        )


@pytest.mark.parametrize("value", ["", "-1", "+1", "1.0", "1/2", "1" * 25])
def test_strict_snowflake_validation_rejects_invalid_message_ids(tmp_path, value):
    conversation = replace(
        _conversation(),
        messages=(replace(_conversation().messages[0], message_id=value),),
    )

    with pytest.raises(ValueError, match="message_id"):
        ObsidianConversationStore(
            vault_root=tmp_path / "vault", runtime_root=tmp_path
        ).save(
            conversation=conversation,
            participant_resolver=ParticipantResolver({}),
            summary=_summary(),
            meeting_run=None,
        )


def test_secret_and_uncontrolled_mentions_never_reach_any_written_value(tmp_path):
    vault = tmp_path / "vault"
    secret = "token=abc123 @everyone"
    conversation = replace(
        _conversation(content=secret),
        thread_name=secret,
        visibility=secret,
        messages=(
            replace(
                _conversation().messages[0],
                created_at=secret,
                author=DiscordAuthor("300", secret),
            ),
            replace(_conversation(content=secret).messages[1], content=secret),
        ),
    )
    meeting = replace(
        _meeting(),
        meeting_run_id=secret,
        trigger={"text": secret},
        metadata={"artifact_paths": [secret]},
    )

    ObsidianConversationStore(vault_root=vault, runtime_root=tmp_path).save(
        conversation=conversation,
        participant_resolver=ParticipantResolver({}),
        summary=replace(
            _summary(),
            summary=secret,
            decisions=(secret,),
            action_items=(ActionItem(secret, secret),),
        ),
        meeting_run=meeting,
    )

    written = [*vault.rglob("*.md"), *tmp_path.glob("runtime/discord_save/*.json")]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in written)
    assert "abc123" not in combined
    assert "@everyone" not in combined
    assert "[REDACTED_SECRET]" in combined


def test_quoted_and_credential_assignments_are_redacted_in_raw_and_canonical(
    tmp_path,
):
    raw_secret = 'Keep {"password":"RAW_PASSWORD","name":"Oracle"} auth: "RAW AUTH"'
    canonical_secret = (
        'Canonical {"token":"CANONICAL_TOKEN","topic":"launch"} '
        'credential="CANONICAL_CREDENTIAL"'
    )

    result = ObsidianConversationStore(
        vault_root=tmp_path / "vault", runtime_root=tmp_path
    ).save(
        conversation=_conversation(content=raw_secret),
        participant_resolver=ParticipantResolver({}),
        summary=replace(_summary(), summary=canonical_secret),
    )

    raw = (tmp_path / "vault" / result.snapshot_path).read_text(encoding="utf-8")
    canonical = (tmp_path / "vault" / result.canonical_path).read_text(encoding="utf-8")
    assert (
        "- 2026-07-13T01:05:00+00:00 Content Lead: Keep "
        '\\{[REDACTED_SECRET],"name":"Oracle"\\} [REDACTED_SECRET] '
        "(message ID: `2`)"
    ) in raw
    assert (
        'Canonical \\{[REDACTED_SECRET],"topic":"launch"\\} [REDACTED_SECRET]'
    ) in canonical
    for secret in (
        "RAW_PASSWORD",
        "RAW AUTH",
        "CANONICAL_TOKEN",
        "CANONICAL_CREDENTIAL",
    ):
        assert secret not in raw
        assert secret not in canonical


def test_yaml_credential_scalars_are_redacted_in_raw_and_canonical(tmp_path):
    raw_secret = (
        "password: 'raw''s secret'\n"
        "token: plain raw secret\n"
        "credential: |-\n"
        " literal raw secret\n"
        "auth: >+1\n"
        " folded raw secret\n"
        "note: keep raw context"
    )
    canonical_secret = (
        "password: 'canonical''s secret'\n"
        "token: plain canonical secret\n"
        "credential: |-\n"
        " literal canonical secret\n"
        "auth: >+1\n"
        " folded canonical secret\n"
        "note: keep canonical context"
    )

    result = ObsidianConversationStore(
        vault_root=tmp_path / "vault", runtime_root=tmp_path
    ).save(
        conversation=_conversation(content=raw_secret),
        participant_resolver=ParticipantResolver({}),
        summary=replace(_summary(), summary=canonical_secret),
    )

    raw = (tmp_path / "vault" / result.snapshot_path).read_text(encoding="utf-8")
    canonical = (tmp_path / "vault" / result.canonical_path).read_text(encoding="utf-8")
    assert (
        "- 2026-07-13T01:05:00+00:00 Content Lead: "
        "[REDACTED_SECRET] <br> [REDACTED_SECRET] <br> "
        "[REDACTED_SECRET] <br>  [REDACTED_SECRET] <br> "
        "[REDACTED_SECRET] <br>  [REDACTED_SECRET] <br> "
        "note: keep raw context (message ID: `2`)"
    ) in raw
    assert (
        "[REDACTED_SECRET] <br> [REDACTED_SECRET] <br> "
        "[REDACTED_SECRET] <br>  [REDACTED_SECRET] <br> "
        "[REDACTED_SECRET] <br>  [REDACTED_SECRET] <br> "
        "note: keep canonical context"
    ) in canonical
    for secret in (
        "raw's secret",
        "plain raw secret",
        "literal raw secret",
        "folded raw secret",
        "canonical's secret",
        "plain canonical secret",
        "literal canonical secret",
        "folded canonical secret",
    ):
        assert secret not in raw
        assert secret not in canonical


def test_namespaced_quoted_and_flow_yaml_secrets_are_redacted_in_all_pages(tmp_path):
    raw_secret = (
        "DISCORD_BOT_TOKEN=raw-token\n"
        '"client_secret": |-\n'
        "  raw block secret\n"
        "settings: {aws_secret_access_key: plain raw flow secret, region: keep-raw}\n"
        "note: keep raw context"
    )
    canonical_secret = (
        "github_token=canonical-token\n"
        "'db_password': >-\n"
        "  canonical block secret\n"
        "settings: {password: plain canonical flow secret, mode: keep-canonical}\n"
        "note: keep canonical context"
    )

    result = ObsidianConversationStore(
        vault_root=tmp_path / "vault", runtime_root=tmp_path
    ).save(
        conversation=_conversation(content=raw_secret),
        participant_resolver=ParticipantResolver({}),
        summary=replace(_summary(), summary=canonical_secret),
    )

    raw = (tmp_path / "vault" / result.snapshot_path).read_text(encoding="utf-8")
    canonical = (tmp_path / "vault" / result.canonical_path).read_text(encoding="utf-8")
    for secret in (
        "raw-token",
        "raw block secret",
        "plain raw flow secret",
        "canonical-token",
        "canonical block secret",
        "plain canonical flow secret",
    ):
        assert secret not in raw
        assert secret not in canonical
    assert "region: keep-raw" in raw
    assert "note: keep raw context" in raw
    assert "mode: keep-canonical" in canonical
    assert "note: keep canonical context" in canonical


def test_camel_case_secrets_are_redacted_without_losing_safe_page_fields(tmp_path):
    raw_secret = (
        "secretAccessKey=raw aws secret region=ap-northeast-2 "
        "authorization_url=https://accounts.example.test/oauth2/auth?mode=safe"
    )
    canonical_secret = (
        "{clientSecret: canonical secret, auth_method: oauth2, "
        "password_policy: rotate every 90 days}"
    )

    result = ObsidianConversationStore(
        vault_root=tmp_path / "vault", runtime_root=tmp_path
    ).save(
        conversation=_conversation(content=raw_secret),
        participant_resolver=ParticipantResolver({}),
        summary=replace(_summary(), summary=canonical_secret),
    )

    raw = (tmp_path / "vault" / result.snapshot_path).read_text(encoding="utf-8")
    canonical = (tmp_path / "vault" / result.canonical_path).read_text(encoding="utf-8")
    assert (
        "Content Lead: [REDACTED_SECRET] region=ap-northeast-2 "
        "authorization\\_url=https://accounts.example.test/oauth2/auth?mode=safe "
        "(message ID: `2`)"
    ) in raw
    assert (
        "\\{[REDACTED_SECRET], auth\\_method: oauth2, "
        "password\\_policy: rotate every 90 days\\}"
    ) in canonical
    for secret in ("raw aws secret", "canonical secret"):
        assert secret not in raw
        assert secret not in canonical


def test_participants_resolve_by_id_and_keep_discord_metadata(tmp_path):
    resolver = ParticipantResolver(
        {"400": BotIdentity(role="Content Lead", hermes_profile="content")}
    )

    result = ObsidianConversationStore(
        vault_root=tmp_path / "vault", runtime_root=tmp_path
    ).save(
        conversation=_conversation(),
        participant_resolver=resolver,
        summary=_summary(),
    )

    snapshot = (tmp_path / "vault" / result.snapshot_path).read_text(encoding="utf-8")
    assert "Content Lead: Start in 3 days as decided" in snapshot
    assert "discord_name: Content Lead" in snapshot
    assert "discord_user_id: 400" in snapshot
    assert "hermes_profile: content" in snapshot


def test_urls_and_attachment_metadata_are_stored_without_attachment_content(tmp_path):
    message = replace(
        _conversation().messages[-1],
        content="See https://example.test/spec?q=one",
        attachments=(
            DiscordAttachment(
                attachment_id="500",
                filename="brief.pdf",
                content_type="application/pdf",
                size=42,
                url="https://cdn.example.test/brief.pdf",
            ),
        ),
    )
    conversation = replace(
        _conversation(), messages=(_conversation().messages[0], message)
    )

    result = ObsidianConversationStore(
        vault_root=tmp_path / "vault", runtime_root=tmp_path
    ).save(
        conversation=conversation,
        participant_resolver=ParticipantResolver({}),
        summary=_summary(),
    )

    snapshot = (tmp_path / "vault" / result.snapshot_path).read_text(encoding="utf-8")
    assert "https://example.test/spec?q=one" in snapshot
    assert "attachment_id: 500" in snapshot
    assert "filename: brief.pdf" in snapshot
    assert "content_type: application/pdf" in snapshot
    assert "size: 42" in snapshot
    assert "url: https://cdn.example.test/brief.pdf" in snapshot


def test_url_userinfo_never_reaches_raw_or_canonical_markdown(tmp_path):
    message = replace(
        _conversation().messages[-1],
        content="See https://alice:p%40ss@example.test/spec",
        attachments=(
            DiscordAttachment(
                attachment_id="500",
                filename="brief.pdf",
                content_type="application/pdf",
                size=42,
                url="https://bob%3Aencoded%40cdn.example.test/brief.pdf",
            ),
        ),
    )
    conversation = replace(
        _conversation(), messages=(_conversation().messages[0], message)
    )

    result = ObsidianConversationStore(
        vault_root=tmp_path / "vault", runtime_root=tmp_path
    ).save(
        conversation=conversation,
        participant_resolver=ParticipantResolver({}),
        summary=replace(
            _summary(), summary="See https://summary:secret@example.test/result"
        ),
    )

    raw = (tmp_path / "vault" / result.snapshot_path).read_text(encoding="utf-8")
    canonical = (tmp_path / "vault" / result.canonical_path).read_text(encoding="utf-8")
    combined = raw + canonical
    assert "alice" not in combined
    assert "p%40ss" not in combined
    assert "bob" not in combined
    assert "encoded" not in combined
    assert "summary:secret" not in combined
    assert "https://example.test/spec" in combined
    assert "https://cdn.example.test/brief.pdf" in combined
    assert "https://example.test/result" in combined


def test_meeting_evidence_contains_only_available_fields_and_paths(tmp_path):
    artifact = (
        tmp_path
        / "runtime"
        / "meeting_runs"
        / "mr-1"
        / "worker_outputs"
        / "worker-1.md"
    )
    artifact.parent.mkdir(parents=True)
    artifact.write_text("existing evidence", encoding="utf-8")
    meeting = replace(
        _meeting(),
        worker_task_ids=("worker-1",),
        validation_ids=("validation-1",),
        metadata={
            "artifact_paths": ["runtime/meeting_runs/mr-1/worker_outputs/worker-1.md"]
        },
    )

    result = ObsidianConversationStore(
        vault_root=tmp_path / "vault", runtime_root=tmp_path
    ).save(
        conversation=_conversation(),
        participant_resolver=ParticipantResolver({}),
        summary=_summary(),
        meeting_run=meeting,
    )

    canonical = (tmp_path / "vault" / result.canonical_path).read_text(encoding="utf-8")
    assert "## MeetingRun evidence" in canonical
    assert "worker-1" in canonical
    assert "validation-1" in canonical
    assert "runtime/meeting_runs/mr-1/worker_outputs/worker-1.md" in canonical
    assert "final_report" not in canonical


def test_empty_conversation_fails_with_fixed_error_without_writing(tmp_path):
    conversation = replace(_conversation(), messages=())

    with pytest.raises(ValueError, match="^empty_conversation$"):
        ObsidianConversationStore(
            vault_root=tmp_path / "vault", runtime_root=tmp_path
        ).save(
            conversation=conversation,
            participant_resolver=ParticipantResolver({}),
            summary=_summary(),
        )

    assert not (tmp_path / "vault").exists()
    assert not (tmp_path / "runtime" / "discord_save").exists()


def test_result_and_checkpoint_paths_are_posix_relative_on_windows(tmp_path):
    result = ObsidianConversationStore(
        vault_root=tmp_path / "vault", runtime_root=tmp_path
    ).save(
        conversation=_conversation(),
        participant_resolver=ParticipantResolver({}),
        summary=_summary(),
    )

    checkpoint = json.loads(
        (tmp_path / "runtime" / "discord_save" / "200.json").read_text(encoding="utf-8")
    )
    for path in (
        result.snapshot_path,
        result.canonical_path,
        checkpoint["canonical_path"],
        *checkpoint["snapshot_paths"],
    ):
        assert "\\" not in path
        assert not path.startswith("/")
        assert ":" not in path


def test_atomic_write_cleans_temporary_file_when_replace_fails(tmp_path, monkeypatch):
    destination = tmp_path / "vault" / "wiki" / "conversations" / "page.md"
    destination.parent.mkdir(parents=True)
    destination.write_text("original", encoding="utf-8")
    temporary_paths = []

    def fail_replace(source, _destination):
        temporary_paths.append(source)
        raise OSError("replace failed")

    monkeypatch.setattr(module.os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        module._atomic_write_text(destination, "replacement")

    assert destination.read_text(encoding="utf-8") == "original"
    assert temporary_paths
    assert not temporary_paths[0].exists()


@pytest.mark.parametrize(
    ("field", "attack_path"),
    [
        ("canonical_path", "wiki/log.md"),
        ("canonical_path", "wiki/conversations/not-stable__200.md"),
        ("snapshot_paths", ["wiki/index.md"]),
        ("snapshot_paths", ["raw/chat-logs/2026-07-13_plan__999__2.md"]),
        ("snapshot_paths", ["raw/chat-logs/2026-07-13_plan__200__3.md"]),
    ],
)
def test_checkpoint_paths_must_match_thread_namespaces_and_identity(
    tmp_path, field, attack_path
):
    store = ObsidianConversationStore(
        vault_root=tmp_path / "vault", runtime_root=tmp_path
    )
    store.save(
        conversation=_conversation(),
        participant_resolver=ParticipantResolver({}),
        summary=_summary(),
    )
    checkpoint_path = tmp_path / "runtime" / "discord_save" / "200.json"
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    checkpoint[field] = attack_path
    checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf-8")

    with pytest.raises(ValueError, match="invalid_checkpoint"):
        store.save(
            conversation=_conversation(),
            participant_resolver=ParticipantResolver({}),
            summary=_summary(),
        )


def test_partial_raw_snapshot_is_removed_when_fsync_fails(tmp_path, monkeypatch):
    store = ObsidianConversationStore(
        vault_root=tmp_path / "vault", runtime_root=tmp_path
    )

    def fail_fsync(_descriptor):
        raise OSError("raw fsync failed")

    monkeypatch.setattr(module.os, "fsync", fail_fsync)

    with pytest.raises(OSError, match="raw fsync failed"):
        store.save(
            conversation=_conversation(),
            participant_resolver=ParticipantResolver({}),
            summary=_summary(),
        )

    assert list((tmp_path / "vault" / "raw" / "chat-logs").glob("*.md")) == []


def test_retry_adopts_complete_raw_snapshot_after_later_write_failure(
    tmp_path, monkeypatch
):
    vault = tmp_path / "vault"
    store = ObsidianConversationStore(vault_root=vault, runtime_root=tmp_path)
    original_atomic_write = module._atomic_write_text
    failed = False

    def fail_first_canonical(path, text):
        nonlocal failed
        if not failed and path.parent.name == "conversations":
            failed = True
            raise OSError("canonical write failed")
        return original_atomic_write(path, text)

    monkeypatch.setattr(module, "_atomic_write_text", fail_first_canonical)

    with pytest.raises(OSError, match="canonical write failed"):
        store.save(
            conversation=_conversation(),
            participant_resolver=ParticipantResolver({}),
            summary=_summary(),
        )
    raw_paths = list((vault / "raw" / "chat-logs").glob("*.md"))
    assert len(raw_paths) == 1
    original_raw = raw_paths[0].read_text(encoding="utf-8")

    result = store.save(
        conversation=_conversation(),
        participant_resolver=ParticipantResolver({}),
        summary=_summary(),
    )

    assert result.status == "created"
    assert raw_paths[0].read_text(encoding="utf-8") == original_raw
    assert (vault / result.canonical_path).exists()
    assert (tmp_path / "runtime" / "discord_save" / "200.json").exists()


def test_same_latest_retry_after_thread_rename_reuses_existing_orphan(
    tmp_path, monkeypatch
):
    vault = tmp_path / "vault"
    store = ObsidianConversationStore(vault_root=vault, runtime_root=tmp_path)
    original_atomic_write = module._atomic_write_text
    failed = False

    def fail_first_canonical(path, text):
        nonlocal failed
        if not failed and path.parent.name == "conversations":
            failed = True
            raise OSError("canonical write failed")
        return original_atomic_write(path, text)

    monkeypatch.setattr(module, "_atomic_write_text", fail_first_canonical)
    original_conversation = replace(_conversation(), thread_name="Original name")
    with pytest.raises(OSError, match="canonical write failed"):
        store.save(
            conversation=original_conversation,
            participant_resolver=ParticipantResolver({}),
            summary=_summary(),
        )
    original_raw = next((vault / "raw" / "chat-logs").glob("*.md"))
    original_bytes = original_raw.read_bytes()
    original_relative = original_raw.relative_to(vault).as_posix()

    renamed_conversation = replace(_conversation(), thread_name="Renamed thread")
    recovered = store.save(
        conversation=renamed_conversation,
        participant_resolver=ParticipantResolver({}),
        summary=_summary(),
    )

    raw_paths = list((vault / "raw" / "chat-logs").glob("*.md"))
    checkpoint = json.loads(
        (tmp_path / "runtime" / "discord_save" / "200.json").read_text(encoding="utf-8")
    )
    assert recovered.status == "created"
    assert recovered.snapshot_path == original_relative
    assert raw_paths == [original_raw]
    assert original_raw.read_bytes() == original_bytes
    assert checkpoint["snapshot_paths"] == [original_relative]

    unchanged = store.save(
        conversation=renamed_conversation,
        participant_resolver=ParticipantResolver({}),
        summary=_summary(),
    )
    assert unchanged.status == "unchanged"
    assert list((vault / "raw" / "chat-logs").glob("*.md")) == [original_raw]


def test_genuinely_ambiguous_existing_snapshot_paths_still_fail_closed(
    tmp_path, monkeypatch
):
    vault = tmp_path / "vault"
    store = ObsidianConversationStore(vault_root=vault, runtime_root=tmp_path)
    original_atomic_write = module._atomic_write_text

    def fail_canonical(path, text):
        if path.parent.name == "conversations":
            raise OSError("canonical write failed")
        return original_atomic_write(path, text)

    monkeypatch.setattr(module, "_atomic_write_text", fail_canonical)
    with pytest.raises(OSError, match="canonical write failed"):
        store.save(
            conversation=replace(_conversation(), thread_name="Original name"),
            participant_resolver=ParticipantResolver({}),
            summary=_summary(),
        )
    original_raw = next((vault / "raw" / "chat-logs").glob("*.md"))
    duplicate_raw = original_raw.with_name(
        original_raw.name.replace("Original-name", "Conflicting-name")
    )
    duplicate_raw.write_bytes(original_raw.read_bytes())

    with pytest.raises(ValueError, match="ambiguous_immutable_snapshot"):
        store.save(
            conversation=replace(_conversation(), thread_name="Renamed thread"),
            participant_resolver=ParticipantResolver({}),
            summary=_summary(),
        )
    assert sorted((vault / "raw" / "chat-logs").glob("*.md")) == sorted(
        [original_raw, duplicate_raw]
    )


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_unchanged_save_fails_safely_when_checkpoint_evidence_is_invalid(
    tmp_path, damage
):
    vault = tmp_path / "vault"
    store = ObsidianConversationStore(vault_root=vault, runtime_root=tmp_path)
    first = store.save(
        conversation=_conversation(),
        participant_resolver=ParticipantResolver({}),
        summary=_summary(),
    )
    snapshot = vault / first.snapshot_path
    if damage == "missing":
        snapshot.unlink()
        expected_error = "missing_immutable_snapshot"
    else:
        original = snapshot.read_text(encoding="utf-8")
        snapshot.write_text(original + "corrupt body\n", encoding="utf-8")
        expected_error = "invalid_immutable_snapshot"

    with pytest.raises((FileNotFoundError, ValueError), match=expected_error):
        store.save(
            conversation=_conversation(),
            participant_resolver=ParticipantResolver({}),
            summary=_summary(),
        )


def test_unchanged_save_repairs_missing_canonical_log_and_index(tmp_path):
    vault = tmp_path / "vault"
    store = ObsidianConversationStore(vault_root=vault, runtime_root=tmp_path)
    first = store.save(
        conversation=_conversation(),
        participant_resolver=ParticipantResolver({}),
        summary=_summary(),
    )
    (vault / first.canonical_path).unlink()
    (vault / "wiki" / "log.md").unlink()
    (vault / "wiki" / "index.md").unlink()

    second = store.save(
        conversation=_conversation(),
        participant_resolver=ParticipantResolver({}),
        summary=_summary(),
    )

    assert second.status == "unchanged"
    assert (vault / second.canonical_path).exists()
    assert "200:2" in (vault / "wiki" / "log.md").read_text(encoding="utf-8")
    assert "oracle-index:200" in (vault / "wiki" / "index.md").read_text(
        encoding="utf-8"
    )


def test_meeting_evidence_ignores_nonexistent_metadata_paths(tmp_path):
    meeting = replace(
        _meeting(),
        metadata={"artifact_paths": ["runtime/meeting_runs/mr-1/missing.md"]},
    )

    result = ObsidianConversationStore(
        vault_root=tmp_path / "vault", runtime_root=tmp_path
    ).save(
        conversation=_conversation(),
        participant_resolver=ParticipantResolver({}),
        summary=_summary(),
        meeting_run=meeting,
    )

    canonical = (tmp_path / "vault" / result.canonical_path).read_text(encoding="utf-8")
    assert "missing.md" not in canonical


def test_meeting_evidence_rejects_symlink_escape(tmp_path):
    outside_dir = tmp_path.parent / f"{tmp_path.name}-artifact-outside"
    outside_dir.mkdir()
    outside = outside_dir / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    link = tmp_path / "runtime" / "meeting_runs" / "mr-1" / "escape"
    link.parent.mkdir(parents=True)
    if os.name == "nt":
        completed = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(outside_dir)],
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stderr
    else:
        link.symlink_to(outside_dir, target_is_directory=True)
    escaped_artifact = link / outside.name
    meeting = replace(_meeting(), metadata={"artifact_paths": [str(escaped_artifact)]})

    try:
        result = ObsidianConversationStore(
            vault_root=tmp_path / "vault", runtime_root=tmp_path
        ).save(
            conversation=_conversation(),
            participant_resolver=ParticipantResolver({}),
            summary=_summary(),
            meeting_run=meeting,
        )
    finally:
        if os.name == "nt":
            os.rmdir(link)
        else:
            link.unlink()
        outside.unlink()
        outside_dir.rmdir()

    canonical = (tmp_path / "vault" / result.canonical_path).read_text(encoding="utf-8")
    assert "escape/outside.md" not in canonical
    assert "outside.md" not in canonical


def test_same_latest_id_concurrent_saves_create_then_converge_unchanged(tmp_path):
    vault = tmp_path / "vault"
    barrier = threading.Barrier(2)

    def save_once():
        barrier.wait()
        return ObsidianConversationStore(vault_root=vault, runtime_root=tmp_path).save(
            conversation=_conversation(),
            participant_resolver=ParticipantResolver({}),
            summary=_summary(),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: save_once(), range(2)))

    assert sorted(result.status for result in results) == ["created", "unchanged"]
    assert len(list((vault / "raw" / "chat-logs").glob("*.md"))) == 1
    checkpoint = json.loads(
        (tmp_path / "runtime" / "discord_save" / "200.json").read_text(encoding="utf-8")
    )
    assert len(checkpoint["snapshot_paths"]) == 1


def test_different_latest_id_concurrent_saves_never_regress_or_lose_paths(
    tmp_path, monkeypatch
):
    vault = tmp_path / "vault"
    store = ObsidianConversationStore(vault_root=vault, runtime_root=tmp_path)
    low_raw_started = threading.Event()
    release_low_raw = threading.Event()
    original_write_raw = module.ObsidianConversationStore._write_raw_exclusive

    def gate_low_raw(self, path, markdown):
        if path.name.endswith("__2.md"):
            low_raw_started.set()
            assert release_low_raw.wait(timeout=5)
        return original_write_raw(self, path, markdown)

    monkeypatch.setattr(
        module.ObsidianConversationStore, "_write_raw_exclusive", gate_low_raw
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        low = executor.submit(
            store.save,
            conversation=_conversation("2"),
            participant_resolver=ParticipantResolver({}),
            summary=_summary(),
        )
        assert low_raw_started.wait(timeout=5)
        high = executor.submit(
            store.save,
            conversation=_conversation("3"),
            participant_resolver=ParticipantResolver({}),
            summary=_summary(),
        )
        time.sleep(0.1)
        release_low_raw.set()
        results = [low.result(timeout=5), high.result(timeout=5)]

    assert [result.status for result in results] == ["created", "updated"]
    checkpoint = json.loads(
        (tmp_path / "runtime" / "discord_save" / "200.json").read_text(encoding="utf-8")
    )
    assert checkpoint["latest_message_id"] == "3"
    assert [path.rsplit("__", 1)[-1] for path in checkpoint["snapshot_paths"]] == [
        "2.md",
        "3.md",
    ]


def test_cross_thread_concurrency_preserves_every_log_and_index_record(
    tmp_path, monkeypatch
):
    vault = tmp_path / "vault"
    original_atomic_write = module._atomic_write_text

    def delayed_shared_write(path, text):
        if path.name in {"log.md", "index.md"}:
            time.sleep(0.03)
        return original_atomic_write(path, text)

    monkeypatch.setattr(module, "_atomic_write_text", delayed_shared_write)
    barrier = threading.Barrier(6)

    def save_thread(index):
        thread_id = str(200 + index)
        barrier.wait()
        return ObsidianConversationStore(vault_root=vault, runtime_root=tmp_path).save(
            conversation=replace(
                _conversation(),
                thread_id=thread_id,
                thread_name=f"Thread {thread_id}",
            ),
            participant_resolver=ParticipantResolver({}),
            summary=_summary(),
        )

    with ThreadPoolExecutor(max_workers=6) as executor:
        list(executor.map(save_thread, range(6)))

    log = (vault / "wiki" / "log.md").read_text(encoding="utf-8")
    index = (vault / "wiki" / "index.md").read_text(encoding="utf-8")
    for thread_id in map(str, range(200, 206)):
        assert f"{thread_id}:2" in log
        assert f"oracle-index:{thread_id}" in index


def test_same_latest_id_multiprocess_saves_converge_created_unchanged(tmp_path):
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    results = context.Queue()
    processes = [
        context.Process(
            target=_multiprocess_save_worker,
            kwargs={
                "label": str(index),
                "vault_root": tmp_path / "vault",
                "runtime_root": tmp_path,
                "thread_id": "200",
                "latest_id": "2",
                "start_event": start,
                "result_queue": results,
            },
        )
        for index in range(2)
    ]
    for process in processes:
        process.start()
    start.set()
    outcomes = [results.get(timeout=20) for _process in processes]
    _finish_processes(processes)

    assert all(outcome[1] == "ok" for outcome in outcomes), outcomes
    assert sorted(outcome[2] for outcome in outcomes) == ["created", "unchanged"]
    assert len(list((tmp_path / "vault" / "raw" / "chat-logs").glob("*.md"))) == 1


def test_different_latest_id_multiprocess_saves_do_not_regress(tmp_path):
    context = multiprocessing.get_context("spawn")
    low_start = context.Event()
    high_start = context.Event()
    low_raw_started = context.Event()
    release_low_raw = context.Event()
    high_save_ready = context.Event()
    results = context.Queue()
    low = context.Process(
        target=_multiprocess_save_worker,
        kwargs={
            "label": "low",
            "vault_root": tmp_path / "vault",
            "runtime_root": tmp_path,
            "thread_id": "200",
            "latest_id": "2",
            "start_event": low_start,
            "result_queue": results,
            "raw_started": low_raw_started,
            "release_raw": release_low_raw,
        },
    )
    high = context.Process(
        target=_multiprocess_save_worker,
        kwargs={
            "label": "high",
            "vault_root": tmp_path / "vault",
            "runtime_root": tmp_path,
            "thread_id": "200",
            "latest_id": "3",
            "start_event": high_start,
            "result_queue": results,
            "save_ready": high_save_ready,
        },
    )
    low.start()
    low_start.set()
    assert low_raw_started.wait(timeout=15)
    high.start()
    high_start.set()
    assert high_save_ready.wait(timeout=15)
    time.sleep(1)
    release_low_raw.set()
    outcomes = [results.get(timeout=20) for _process in (low, high)]
    _finish_processes([low, high])

    assert all(outcome[1] == "ok" for outcome in outcomes), outcomes
    assert {outcome[0]: outcome[2] for outcome in outcomes} == {
        "low": "created",
        "high": "updated",
    }
    checkpoint = json.loads(
        (tmp_path / "runtime" / "discord_save" / "200.json").read_text(encoding="utf-8")
    )
    assert checkpoint["latest_message_id"] == "3"
    assert [path.rsplit("__", 1)[-1] for path in checkpoint["snapshot_paths"]] == [
        "2.md",
        "3.md",
    ]


def test_live_process_lock_times_out_without_breaking_owner_and_stale_file_reuses(
    tmp_path, monkeypatch
):
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    vault = tmp_path / "vault"
    store = ObsidianConversationStore(vault_root=vault, runtime_root=tmp_path)
    lock_path = store._runtime_path(
        f".locks/thread-{module._vault_identity(vault)}-200.lock"
    )
    holder = context.Process(
        target=_multiprocess_lock_holder,
        args=(lock_path, ready, release),
    )
    holder.start()
    assert ready.wait(timeout=15)
    monkeypatch.setattr(module, "_INTERPROCESS_LOCK_TIMEOUT_SECONDS", 0.1)
    monkeypatch.setattr(module, "_INTERPROCESS_LOCK_POLL_SECONDS", 0.01)

    with pytest.raises(TimeoutError, match="interprocess_lock_timeout"):
        store.save(
            conversation=_conversation(),
            participant_resolver=ParticipantResolver({}),
            summary=_summary(),
        )
    assert holder.is_alive()
    assert lock_path.exists()

    release.set()
    _finish_processes([holder])
    result = store.save(
        conversation=_conversation(),
        participant_resolver=ParticipantResolver({}),
        summary=_summary(),
    )
    assert result.status == "created"
    assert lock_path.exists()


def test_cross_thread_multiprocess_saves_preserve_log_and_index(tmp_path):
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    results = context.Queue()
    thread_ids = [str(value) for value in range(200, 207)]
    processes = [
        context.Process(
            target=_multiprocess_save_worker,
            kwargs={
                "label": thread_id,
                "vault_root": tmp_path / "vault",
                "runtime_root": tmp_path,
                "thread_id": thread_id,
                "latest_id": "2",
                "start_event": start,
                "result_queue": results,
                "delay_shared_writes": True,
            },
        )
        for thread_id in thread_ids
    ]
    for process in processes:
        process.start()
    start.set()
    outcomes = [results.get(timeout=30) for _process in processes]
    _finish_processes(processes)

    assert all(outcome[1] == "ok" for outcome in outcomes), outcomes
    log = (tmp_path / "vault" / "wiki" / "log.md").read_text(encoding="utf-8")
    index = (tmp_path / "vault" / "wiki" / "index.md").read_text(encoding="utf-8")
    for thread_id in thread_ids:
        assert f"oracle-log:{thread_id}:2" in log
        assert f"oracle-index:{thread_id}" in index


def test_later_request_adopts_all_valid_earlier_orphan_snapshots(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    store = ObsidianConversationStore(vault_root=vault, runtime_root=tmp_path)
    original_atomic_write = module._atomic_write_text
    failed = False

    def fail_first_canonical(path, text):
        nonlocal failed
        if not failed and path.parent.name == "conversations":
            failed = True
            raise OSError("canonical write failed")
        return original_atomic_write(path, text)

    monkeypatch.setattr(module, "_atomic_write_text", fail_first_canonical)
    with pytest.raises(OSError, match="canonical write failed"):
        store.save(
            conversation=_conversation("2"),
            participant_resolver=ParticipantResolver({}),
            summary=_summary(),
        )

    result = store.save(
        conversation=_conversation("3"),
        participant_resolver=ParticipantResolver({}),
        summary=_summary(),
    )

    checkpoint = json.loads(
        (tmp_path / "runtime" / "discord_save" / "200.json").read_text(encoding="utf-8")
    )
    assert result.status == "created"
    assert [path.rsplit("__", 1)[-1] for path in checkpoint["snapshot_paths"]] == [
        "2.md",
        "3.md",
    ]
    canonical = (vault / result.canonical_path).read_text(encoding="utf-8")
    assert all(path in canonical for path in checkpoint["snapshot_paths"])


def test_earliest_recovered_orphan_restores_stable_canonical_identity(
    tmp_path, monkeypatch
):
    vault = tmp_path / "vault"
    store = ObsidianConversationStore(vault_root=vault, runtime_root=tmp_path)
    original_atomic_write = module._atomic_write_text
    failed = False

    def fail_first_canonical(path, text):
        nonlocal failed
        if not failed and path.parent.name == "conversations":
            failed = True
            raise OSError("canonical write failed")
        return original_atomic_write(path, text)

    monkeypatch.setattr(module, "_atomic_write_text", fail_first_canonical)
    original_conversation = replace(_conversation("2"), thread_name="Original name")
    with pytest.raises(OSError, match="canonical write failed"):
        store.save(
            conversation=original_conversation,
            participant_resolver=ParticipantResolver({}),
            summary=_summary(),
        )
    original_raw = next((vault / "raw" / "chat-logs").glob("*.md"))
    held_raw = tmp_path / original_raw.name
    original_raw.replace(held_raw)

    newer = store.save(
        conversation=_conversation("3"),
        participant_resolver=ParticipantResolver({}),
        summary=_summary(),
    )
    old_canonical = vault / newer.canonical_path
    held_raw.replace(original_raw)

    recovered = store.save(
        conversation=_conversation("4"),
        participant_resolver=ParticipantResolver({}),
        summary=_summary(),
    )
    checkpoint = json.loads(
        (tmp_path / "runtime" / "discord_save" / "200.json").read_text(encoding="utf-8")
    )
    expected_canonical = f"wiki/conversations/{original_raw.stem.rsplit('__', 1)[0]}.md"
    assert recovered.canonical_path == expected_canonical
    assert checkpoint["canonical_path"] == expected_canonical
    assert not old_canonical.exists()
    assert [path.rsplit("__", 1)[-1] for path in checkpoint["snapshot_paths"]] == [
        "2.md",
        "3.md",
        "4.md",
    ]

    unchanged = store.save(
        conversation=_conversation("4"),
        participant_resolver=ParticipantResolver({}),
        summary=_summary(),
    )
    assert unchanged.status == "unchanged"


def test_runtime_checkpoint_namespace_rejects_directory_link_escape(tmp_path):
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside-runtime"
    runtime_parent = workspace / "runtime"
    link = runtime_parent / "discord_save"
    runtime_parent.mkdir(parents=True)
    outside.mkdir()
    if os.name == "nt":
        completed = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stderr
    else:
        link.symlink_to(outside, target_is_directory=True)

    try:
        with pytest.raises(ValueError, match="path escapes root"):
            ObsidianConversationStore(
                vault_root=workspace / "vault", runtime_root=workspace
            ).save(
                conversation=_conversation(),
                participant_resolver=ParticipantResolver({}),
                summary=_summary(),
            )
        assert list(outside.iterdir()) == []
    finally:
        if os.name == "nt":
            os.rmdir(link)
        else:
            link.unlink()


def test_markdown_and_comment_markers_from_user_values_are_escaped(tmp_path):
    payload = (
        "Readable text\n## Injected heading\n- injected item\n"
        "[[injected-note]] ![[injected-embed]] "
        "<!-- oracle-log:999:9 -->"
    )
    conversation = replace(
        _conversation(content=payload),
        thread_name=payload,
        messages=(
            replace(
                _conversation().messages[0],
                author=DiscordAuthor("300", payload),
            ),
            _conversation(content=payload).messages[1],
        ),
    )
    summary = replace(
        _summary(),
        summary=payload,
        decisions=(payload,),
        action_items=(ActionItem(payload, payload),),
    )

    result = ObsidianConversationStore(
        vault_root=tmp_path / "vault", runtime_root=tmp_path
    ).save(
        conversation=conversation,
        participant_resolver=ParticipantResolver({}),
        summary=summary,
    )

    raw = (tmp_path / "vault" / result.snapshot_path).read_text(encoding="utf-8")
    raw_body = raw.split("---", 2)[-1]
    canonical = (tmp_path / "vault" / result.canonical_path).read_text(encoding="utf-8")
    canonical_body = canonical.split("---", 2)[-1]
    log = (tmp_path / "vault" / "wiki" / "log.md").read_text(encoding="utf-8")
    index = (tmp_path / "vault" / "wiki" / "index.md").read_text(encoding="utf-8")
    combined = "\n".join((raw_body, canonical_body, log, index))
    assert "Readable text" in combined
    assert "\n## Injected heading" not in combined
    assert "\n- injected item" not in combined
    assert "[[injected-note]]" not in combined
    assert "![[injected-embed]]" not in combined
    assert "<!-- oracle-log:999:9 -->" not in combined
    assert "<!-- oracle-log:200:2 -->" in log
    assert "<!-- oracle-index:200 -->" in index


def test_later_unimportant_save_removes_stale_index_record(tmp_path):
    vault = tmp_path / "vault"
    store = ObsidianConversationStore(vault_root=vault, runtime_root=tmp_path)
    store.save(
        conversation=_conversation(),
        participant_resolver=ParticipantResolver({}),
        summary=_summary(),
    )
    assert "oracle-index:200" in (vault / "wiki" / "index.md").read_text(
        encoding="utf-8"
    )

    store.save(
        conversation=_conversation("3"),
        participant_resolver=ParticipantResolver({}),
        summary=_summary(important=False),
    )

    assert "oracle-index:200" not in (vault / "wiki" / "index.md").read_text(
        encoding="utf-8"
    )


def test_signed_message_and_attachment_urls_redact_secrets_but_keep_useful_parts(
    tmp_path,
):
    message_url = (
        "https://example.test/spec?foo=keep&X-Amz-Signature=AMZSECRET&"
        "sig=SIGSECRET&Signature=SIGNATURESECRET&TOKEN=TOKENURLSECRET&"
        "key=KEYSECRET#access_token=FRAGMENTSECRET&section=part"
    )
    attachment_url = (
        "https://cdn.example.test/file.pdf?hm=HMSECRET&width=100&"
        "Auth=AUTHSECRET&Password=PASSWORDSECRET"
    )
    message = replace(
        _conversation().messages[-1],
        content=f"Review {message_url}",
        attachments=(
            DiscordAttachment(
                attachment_id="500",
                filename="brief.pdf",
                content_type="application/pdf",
                size=42,
                url=attachment_url,
            ),
        ),
    )
    conversation = replace(
        _conversation(), messages=(_conversation().messages[0], message)
    )

    ObsidianConversationStore(
        vault_root=tmp_path / "vault", runtime_root=tmp_path
    ).save(
        conversation=conversation,
        participant_resolver=ParticipantResolver({}),
        summary=_summary(),
    )

    combined = "\n".join(
        path.read_text(encoding="utf-8") for path in (tmp_path / "vault").rglob("*.md")
    )
    for secret in (
        "AMZSECRET",
        "SIGSECRET",
        "SIGNATURESECRET",
        "TOKENURLSECRET",
        "KEYSECRET",
        "FRAGMENTSECRET",
        "HMSECRET",
        "AUTHSECRET",
        "PASSWORDSECRET",
    ):
        assert secret not in combined
    assert "foo=keep" in combined
    assert "section=part" in combined
    assert "width=100" in combined
    assert "[REDACTED_SECRET]" in combined
