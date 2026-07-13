from __future__ import annotations

import json
from dataclasses import replace

import pytest

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


def test_meeting_evidence_contains_only_available_fields_and_paths(tmp_path):
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
    from src.runtime_architecture_v2 import obsidian_conversations as module

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
