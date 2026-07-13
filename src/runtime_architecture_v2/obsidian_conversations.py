"""Immutable Discord evidence and canonical Obsidian conversation pages."""

from __future__ import annotations

import json
import os
import re
import tempfile
import unicodedata
from collections.abc import Iterable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from .conversation_summary import ConversationSummary
from .discord_conversation import (
    DiscordAttachment,
    DiscordConversation,
    ParticipantIdentity,
    ParticipantResolver,
)
from .knowledge import sanitize_knowledge_text
from .schemas import MeetingRun

_SNOWFLAKE_RE = re.compile(r"^[0-9]{1,24}$")
_URL_RE = re.compile(r'https?://[^\s<>"`]+')
_FILENAME_SEPARATOR_RE = re.compile(r"[\\/:*?\"<>|]+")
_FILENAME_OTHER_RE = re.compile(r"[^\w.-]+", re.UNICODE)
_MEETING_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")


@dataclass(frozen=True)
class ObsidianSaveResult:
    status: str
    classification: str
    new_message_count: int
    snapshot_path: str
    canonical_path: str
    one_line_summary: str


class ObsidianConversationStore:
    """Persist one Discord thread without mutating its raw evidence."""

    def __init__(self, *, vault_root: str | Path, runtime_root: str | Path) -> None:
        self.vault_root = Path(vault_root)
        self.workspace_root = Path(runtime_root)
        self.runtime_root = self.workspace_root / "runtime" / "discord_save"

    def save(
        self,
        *,
        conversation: DiscordConversation,
        participant_resolver: ParticipantResolver,
        summary: ConversationSummary,
        meeting_run: MeetingRun | None = None,
    ) -> ObsidianSaveResult:
        if not conversation.messages:
            raise ValueError("empty_conversation")
        _validate_conversation_ids(conversation)

        messages = conversation.messages
        first_message_id = min(
            messages, key=lambda item: int(item.message_id)
        ).message_id
        latest_message_id = max(
            messages, key=lambda item: int(item.message_id)
        ).message_id
        classification = "meeting" if meeting_run is not None else "conversation"
        one_line_summary = _safe(summary.summary)
        checkpoint_path = self.runtime_root / f"{conversation.thread_id}.json"
        checkpoint = self._load_checkpoint(checkpoint_path, conversation.thread_id)

        if checkpoint is not None:
            previous_latest = str(checkpoint["latest_message_id"])
            canonical_relative = str(checkpoint["canonical_path"])
            snapshot_paths = [str(path) for path in checkpoint["snapshot_paths"]]
            _contained_path(self.vault_root, canonical_relative)
            for path in snapshot_paths:
                _contained_path(self.vault_root, path)
            if previous_latest == latest_message_id:
                if not snapshot_paths:
                    raise ValueError("invalid_checkpoint")
                return ObsidianSaveResult(
                    status="unchanged",
                    classification=classification,
                    new_message_count=0,
                    snapshot_path=snapshot_paths[-1],
                    canonical_path=canonical_relative,
                    one_line_summary=one_line_summary,
                )
            if int(latest_message_id) <= int(previous_latest):
                raise ValueError("latest_message_id_not_newer")
            status = "updated"
            new_message_count = sum(
                int(message.message_id) > int(previous_latest) for message in messages
            )
        else:
            status = "created"
            new_message_count = len(messages)
            snapshot_paths = []
            canonical_relative = self._canonical_relative_path(conversation)

        snapshot_relative = self._snapshot_relative_path(
            conversation, latest_message_id
        )
        snapshot_path = _contained_path(self.vault_root, snapshot_relative)
        canonical_path = _contained_path(self.vault_root, canonical_relative)
        if snapshot_path.exists():
            raise FileExistsError(f"raw snapshot already exists: {snapshot_relative}")

        participants = _resolve_participants(conversation, participant_resolver)
        saved_at = _now_iso()
        raw_markdown = _render_raw_snapshot(
            conversation=conversation,
            participants=participants,
            classification=classification,
            meeting_run=meeting_run,
            saved_at=saved_at,
            first_message_id=first_message_id,
            latest_message_id=latest_message_id,
        )
        self._write_raw_exclusive(snapshot_path, raw_markdown)

        all_snapshot_paths = [*snapshot_paths, snapshot_relative]
        canonical_markdown = _render_canonical_page(
            conversation=conversation,
            participants=participants,
            summary=summary,
            classification=classification,
            meeting_run=meeting_run,
            snapshot_paths=all_snapshot_paths,
            artifact_paths=self._meeting_artifact_paths(meeting_run),
        )
        _atomic_write_text(canonical_path, canonical_markdown)
        self._update_log(
            conversation=conversation,
            canonical_relative=canonical_relative,
            one_line_summary=one_line_summary,
            latest_message_id=latest_message_id,
            saved_at=saved_at,
        )
        if meeting_run is not None or summary.important:
            self._update_index(
                conversation=conversation,
                canonical_relative=canonical_relative,
                one_line_summary=one_line_summary,
                meeting_run=meeting_run,
            )

        _atomic_write_json(
            checkpoint_path,
            {
                "thread_id": conversation.thread_id,
                "latest_message_id": latest_message_id,
                "canonical_path": canonical_relative,
                "snapshot_paths": all_snapshot_paths,
            },
        )
        return ObsidianSaveResult(
            status=status,
            classification=classification,
            new_message_count=new_message_count,
            snapshot_path=snapshot_relative,
            canonical_path=canonical_relative,
            one_line_summary=one_line_summary,
        )

    def _canonical_relative_path(self, conversation: DiscordConversation) -> str:
        date = _conversation_date(conversation)
        title = _filename_component(conversation.thread_name)
        return f"wiki/conversations/{date}_{title}__{conversation.thread_id}.md"

    def _snapshot_relative_path(
        self, conversation: DiscordConversation, latest_message_id: str
    ) -> str:
        date = _conversation_date(conversation)
        title = _filename_component(conversation.thread_name)
        return (
            f"raw/chat-logs/{date}_{title}__{conversation.thread_id}"
            f"__{latest_message_id}.md"
        )

    def _load_checkpoint(
        self, path: Path, expected_thread_id: str
    ) -> dict[str, Any] | None:
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise TypeError
            if set(payload) != {
                "thread_id",
                "latest_message_id",
                "canonical_path",
                "snapshot_paths",
            }:
                raise TypeError
            if payload["thread_id"] != expected_thread_id:
                raise TypeError
            _validate_snowflake(str(payload["latest_message_id"]), "latest_message_id")
            if not isinstance(payload["canonical_path"], str):
                raise TypeError
            if not isinstance(payload["snapshot_paths"], list) or not all(
                isinstance(value, str) for value in payload["snapshot_paths"]
            ):
                raise TypeError
            return payload
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise ValueError("invalid_checkpoint") from exc

    def _write_raw_exclusive(self, path: Path, markdown: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(markdown)
            handle.flush()
            os.fsync(handle.fileno())

    def _update_log(
        self,
        *,
        conversation: DiscordConversation,
        canonical_relative: str,
        one_line_summary: str,
        latest_message_id: str,
        saved_at: str,
    ) -> None:
        path = _contained_path(self.vault_root, "wiki/log.md")
        key = _safe(f"{conversation.thread_id}:{latest_message_id}")
        existing = path.read_text(encoding="utf-8") if path.exists() else "# Log\n"
        if f"<!-- {key} -->" in existing:
            return
        line = (
            f"- {_safe(saved_at)} [{_safe(conversation.thread_name)}]"
            f"({_safe(canonical_relative)}) - {one_line_summary} <!-- {key} -->"
        )
        _atomic_write_text(path, _safe(existing.rstrip() + "\n" + line + "\n"))

    def _update_index(
        self,
        *,
        conversation: DiscordConversation,
        canonical_relative: str,
        one_line_summary: str,
        meeting_run: MeetingRun | None,
    ) -> None:
        path = _contained_path(self.vault_root, "wiki/index.md")
        existing = path.read_text(encoding="utf-8") if path.exists() else "# Index\n"
        key = _safe(f"thread:{conversation.thread_id}")
        meeting_text = (
            f" meeting={_safe(meeting_run.meeting_run_id)}" if meeting_run else ""
        )
        line = (
            f"- [{_safe(conversation.thread_name)}]({_safe(canonical_relative)})"
            f" - {one_line_summary}{meeting_text} <!-- {key} -->"
        )
        lines = [
            line if f"<!-- {key} -->" in existing_line else existing_line
            for existing_line in existing.rstrip().splitlines()
        ]
        if not any(f"<!-- {key} -->" in existing_line for existing_line in lines):
            lines.append(line)
        _atomic_write_text(path, _safe("\n".join(lines) + "\n"))

    def _meeting_artifact_paths(
        self, meeting_run: MeetingRun | None
    ) -> tuple[str, ...]:
        if meeting_run is None:
            return ()
        paths = list(
            _metadata_artifact_paths(meeting_run.metadata, self.workspace_root)
        )
        if _MEETING_ID_RE.fullmatch(meeting_run.meeting_run_id):
            run_dir = (
                self.workspace_root
                / "runtime"
                / "meeting_runs"
                / meeting_run.meeting_run_id
            )
            if run_dir.is_dir():
                paths.extend(
                    path.relative_to(self.workspace_root).as_posix()
                    for path in sorted(run_dir.rglob("*"))
                    if path.is_file()
                )
        return tuple(dict.fromkeys(_safe(path) for path in paths))


def _validate_conversation_ids(conversation: DiscordConversation) -> None:
    _validate_snowflake(conversation.guild_id, "guild_id")
    _validate_snowflake(conversation.parent_channel_id, "parent_channel_id")
    _validate_snowflake(conversation.thread_id, "thread_id")
    for message in conversation.messages:
        _validate_snowflake(message.message_id, "message_id")
        _validate_snowflake(message.author.user_id, "discord_user_id")
        for attachment in message.attachments:
            _validate_snowflake(attachment.attachment_id, "attachment_id")


def _validate_snowflake(value: str, label: str) -> None:
    if not isinstance(value, str) or not _SNOWFLAKE_RE.fullmatch(value):
        raise ValueError(f"invalid {label}")


def _resolve_participants(
    conversation: DiscordConversation, resolver: ParticipantResolver
) -> tuple[ParticipantIdentity, ...]:
    by_id: dict[str, ParticipantIdentity] = {}
    for message in conversation.messages:
        by_id[message.author.user_id] = resolver.resolve(message.author)
    return tuple(by_id.values())


def _render_raw_snapshot(
    *,
    conversation: DiscordConversation,
    participants: tuple[ParticipantIdentity, ...],
    classification: str,
    meeting_run: MeetingRun | None,
    saved_at: str,
    first_message_id: str,
    latest_message_id: str,
) -> str:
    participant_lines = []
    for participant in participants:
        participant_lines.extend(
            (
                f"  - role: {_yaml_value(participant.role)}",
                f"    hermes_profile: {_yaml_value(participant.hermes_profile)}",
                f"    discord_name: {_yaml_value(participant.discord_name)}",
                f"    discord_user_id: {_yaml_value(participant.discord_user_id)}",
            )
        )
    frontmatter = [
        "---",
        f"type: {_yaml_value(classification)}",
        f"saved_at: {_yaml_value(saved_at)}",
        f"discord_guild_id: {_yaml_value(conversation.guild_id)}",
        f"discord_parent_channel_id: {_yaml_value(conversation.parent_channel_id)}",
        f"discord_thread_id: {_yaml_value(conversation.thread_id)}",
        f"discord_thread_name: {_yaml_value(conversation.thread_name)}",
        f"visibility: {_yaml_value(conversation.visibility)}",
        f"first_message_id: {_yaml_value(first_message_id)}",
        f"latest_message_id: {_yaml_value(latest_message_id)}",
        "meeting_run_id: "
        + _yaml_value(meeting_run.meeting_run_id if meeting_run else ""),
        "participants:",
        *(participant_lines or ["  []"]),
        "---",
    ]
    source_url = (
        f"https://discord.com/channels/{conversation.guild_id}/{conversation.thread_id}"
    )
    participant_body = [
        _participant_markdown(participant) for participant in participants
    ]
    transcript = []
    for message in conversation.messages:
        identity = next(
            participant
            for participant in participants
            if participant.discord_user_id == message.author.user_id
        )
        speaker = identity.role or identity.discord_name
        transcript.append(
            f"- {_safe(message.created_at)} {_safe(speaker)}: {_safe(message.content)} "
            f"(message ID: `{_safe(message.message_id)}`)"
        )
    urls = _conversation_urls(conversation)
    attachments = [
        attachment
        for message in conversation.messages
        for attachment in message.attachments
    ]
    body = [
        f"# {_safe(conversation.thread_name)} raw snapshot",
        "",
        "## Source",
        f"- [{_safe(conversation.thread_name)}]({_safe(source_url)})",
        f"- Message range: `{_safe(first_message_id)}` to `{_safe(latest_message_id)}`",
        "",
        "## Participants",
        *_markdown_items(participant_body),
        "",
        "## Transcript",
        *_markdown_items(transcript, already_list=True),
        "",
        "## URLs",
        *_markdown_items(urls),
        "",
        "## Attachments",
        *_attachment_markdown(attachments),
    ]
    return _safe("\n".join([*frontmatter, "", *body]) + "\n")


def _render_canonical_page(
    *,
    conversation: DiscordConversation,
    participants: tuple[ParticipantIdentity, ...],
    summary: ConversationSummary,
    classification: str,
    meeting_run: MeetingRun | None,
    snapshot_paths: list[str],
    artifact_paths: tuple[str, ...],
) -> str:
    source_url = (
        f"https://discord.com/channels/{conversation.guild_id}/{conversation.thread_id}"
    )
    sections = [
        f"# {_safe(conversation.thread_name)}",
        "",
        "## Source",
        f"- Type: {_safe(classification)}",
        f"- Discord: [{_safe(conversation.thread_name)}]({_safe(source_url)})",
        f"- Thread ID: `{_safe(conversation.thread_id)}`",
        "",
        "## One-line summary",
        _safe(summary.summary) or "None.",
        "",
        "## Key ideas",
        *_markdown_items(summary.key_ideas),
        "",
        "## Decisions",
        *_markdown_items(summary.decisions),
        "",
        "## Unresolved questions",
        *_markdown_items(summary.unresolved_questions),
        "",
        "## Action items",
        *_action_item_markdown(summary),
        "",
        "## Participants",
        *_markdown_items(
            _participant_markdown(participant) for participant in participants
        ),
        "",
        "## User perspective",
        _safe(summary.user_perspective) or "None.",
    ]
    if meeting_run is not None:
        sections.extend(
            [
                "",
                "## MeetingRun evidence",
                *_meeting_evidence_markdown(meeting_run, artifact_paths),
            ]
        )
    sections.extend(
        [
            "",
            "## Related notes",
            "- None.",
            "",
            "## Raw snapshots",
            *[f"- [[{_safe(path)}]]" for path in snapshot_paths],
        ]
    )
    return _safe("\n".join(sections) + "\n")


def _participant_markdown(participant: ParticipantIdentity) -> str:
    label = participant.role or participant.discord_name
    profile = (
        f", hermes_profile: {_safe(participant.hermes_profile)}"
        if participant.hermes_profile
        else ""
    )
    role = f" (role: {_safe(participant.role)})" if participant.role else ""
    return (
        f"{_safe(label)}{role} - discord_name: {_safe(participant.discord_name)}, "
        f"discord_user_id: {_safe(participant.discord_user_id)}{profile}"
    )


def _action_item_markdown(summary: ConversationSummary) -> list[str]:
    if not summary.action_items:
        return ["- None."]
    return [
        f"- {_safe(item.text)}"
        + (f" (owner: {_safe(item.owner)})" if item.owner else "")
        for item in summary.action_items
    ]


def _meeting_evidence_markdown(
    meeting_run: MeetingRun, artifact_paths: tuple[str, ...]
) -> list[str]:
    state = getattr(meeting_run.state, "value", str(meeting_run.state))
    lines = [
        f"- MeetingRun ID: `{_safe(meeting_run.meeting_run_id)}`",
        f"- State: `{_safe(state)}`",
    ]
    agenda = str(meeting_run.trigger.get("text") or "")
    if agenda:
        lines.append(f"- Agenda: {_safe(agenda)}")
    id_groups = (
        ("Worker task IDs", meeting_run.worker_task_ids),
        ("Validation IDs", meeting_run.validation_ids),
        ("Projection event IDs", meeting_run.projection_event_ids),
        ("Checkpoint IDs", meeting_run.checkpoint_ids),
    )
    for label, values in id_groups:
        if values:
            lines.append(f"- {label}: " + ", ".join(f"`{_safe(v)}`" for v in values))
    for key, value in sorted(meeting_run.hermes_refs.items()):
        if value:
            lines.append(f"- Hermes {_safe(key)}: `{_safe(value)}`")
    for key in ("created_at", "started_at", "updated_at", "completed_at"):
        value = meeting_run.metadata.get(key)
        if value:
            lines.append(f"- {_safe(key)}: `{_safe(value)}`")
    lines.extend(f"- Artifact: [{path}]({path})" for path in artifact_paths)
    return lines


def _conversation_urls(conversation: DiscordConversation) -> tuple[str, ...]:
    urls = []
    for message in conversation.messages:
        urls.extend(
            match.rstrip(".,;:!?)") for match in _URL_RE.findall(message.content)
        )
    return tuple(dict.fromkeys(_safe(url) for url in urls if url))


def _attachment_markdown(attachments: Iterable[DiscordAttachment]) -> list[str]:
    rendered = []
    for attachment in attachments:
        rendered.extend(
            (
                f"- attachment_id: {_safe(attachment.attachment_id)}",
                f"  filename: {_safe(attachment.filename)}",
                f"  content_type: {_safe(attachment.content_type)}",
                f"  size: {_safe(attachment.size)}",
                f"  url: {_safe(attachment.url)}",
            )
        )
    return rendered or ["- None."]


def _markdown_items(
    values: Iterable[object], *, already_list: bool = False
) -> list[str]:
    safe_values = [_safe(value) for value in values if _safe(value)]
    if not safe_values:
        return ["- None."]
    if already_list:
        return safe_values
    return [f"- {value}" for value in safe_values]


def _metadata_artifact_paths(
    metadata: Mapping[str, Any], workspace_root: Path
) -> tuple[str, ...]:
    found: list[str] = []

    def visit(key: str, value: object) -> None:
        if isinstance(value, Mapping):
            for nested_key, nested_value in value.items():
                visit(str(nested_key), nested_value)
            return
        if isinstance(value, (list, tuple)):
            for nested_value in value:
                visit(key, nested_value)
            return
        if "path" not in key.lower() or not isinstance(value, (str, Path)):
            return
        relative = _artifact_relative_path(value, workspace_root)
        if relative:
            found.append(relative)

    for metadata_key, metadata_value in metadata.items():
        visit(str(metadata_key), metadata_value)
    return tuple(dict.fromkeys(found))


def _artifact_relative_path(value: str | Path, workspace_root: Path) -> str:
    raw = str(value).replace("\\", "/")
    path = Path(raw)
    if path.is_absolute():
        try:
            return path.resolve().relative_to(workspace_root.resolve()).as_posix()
        except ValueError:
            return ""
    pure = PurePosixPath(raw)
    if (
        not raw
        or pure.is_absolute()
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        return ""
    return pure.as_posix()


def _conversation_date(conversation: DiscordConversation) -> str:
    for message in conversation.messages:
        try:
            value = message.created_at.replace("Z", "+00:00")
            return datetime.fromisoformat(value).date().isoformat()
        except ValueError:
            continue
    return datetime.now(UTC).date().isoformat()


def _filename_component(value: str) -> str:
    safe = unicodedata.normalize("NFKC", _safe(value))
    safe = "".join(character for character in safe if ord(character) >= 32)
    safe = _FILENAME_SEPARATOR_RE.sub("-", safe)
    safe = _FILENAME_OTHER_RE.sub("-", safe)
    safe = re.sub(r"[-_.]{2,}", "-", safe).strip("-._")
    return (safe or "thread")[:80].rstrip("-._") or "thread"


def _contained_path(root: Path, relative: str) -> Path:
    if "\\" in relative:
        raise ValueError("unsafe relative path")
    pure = PurePosixPath(relative)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise ValueError("unsafe relative path")
    root_resolved = root.resolve()
    candidate = root.joinpath(*pure.parts)
    try:
        candidate.resolve().relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError("path escapes root") from exc
    return candidate


def _yaml_value(value: object) -> str:
    return json.dumps(_safe(value), ensure_ascii=False)


def _safe(value: object) -> str:
    text = sanitize_knowledge_text(str(value))
    return "".join(
        character
        for character in text
        if character in {"\n", "\t"} or (ord(character) >= 32 and ord(character) != 127)
    )


def _sanitize_json(value: object) -> object:
    if isinstance(value, str):
        return _safe(value)
    if isinstance(value, list):
        return [_sanitize_json(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _sanitize_json(item) for key, item in value.items()}
    return value


def _atomic_write_json(path: Path, payload: Mapping[str, object]) -> None:
    text = json.dumps(
        _sanitize_json(dict(payload)), ensure_ascii=False, indent=2, sort_keys=True
    )
    _atomic_write_text(path, text + "\n")


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    except Exception:
        with suppress(OSError):
            os.close(descriptor)
        temporary_path.unlink(missing_ok=True)
        raise


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()
