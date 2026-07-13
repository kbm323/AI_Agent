"""Immutable Discord evidence and canonical Obsidian conversation pages."""

from __future__ import annotations

import errno
import hashlib
import html
import json
import os
import re
import tempfile
import threading
import time
import unicodedata
from collections.abc import Iterable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

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
_EVIDENCE_HASH_RE = re.compile(r"^[a-f0-9]{64}$")
_LOG_MARKER_RE = re.compile(r"<!-- oracle-log:([0-9]{1,24}):([0-9]{1,24}) -->\s*$")
_INDEX_MARKER_RE = re.compile(r"<!-- oracle-index:([0-9]{1,24}) -->\s*$")
_SECRET_URL_KEYS = {
    "access_token",
    "auth",
    "hm",
    "key",
    "sig",
    "signature",
    "token",
    "x_amz_signature",
}
_LOCKS_GUARD = threading.Lock()
_THREAD_LOCKS: dict[tuple[str, str], threading.RLock] = {}
_VAULT_LOCKS: dict[str, threading.RLock] = {}
_INTERPROCESS_LOCK_TIMEOUT_SECONDS = 30.0
_INTERPROCESS_LOCK_POLL_SECONDS = 0.02


@dataclass(frozen=True)
class ObsidianSaveResult:
    status: str
    classification: str
    new_message_count: int
    snapshot_path: str
    canonical_path: str
    one_line_summary: str


class _InterProcessFileLock:
    """A bounded kernel lock whose persistent file is never treated as ownership."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle: Any | None = None

    def __enter__(self) -> _InterProcessFileLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        deadline = time.monotonic() + _INTERPROCESS_LOCK_TIMEOUT_SECONDS
        while True:
            try:
                _lock_file_nonblocking(handle)
                self._handle = handle
                return self
            except OSError as exc:
                if not _lock_is_busy(exc):
                    handle.close()
                    raise
                if time.monotonic() >= deadline:
                    handle.close()
                    raise TimeoutError("interprocess_lock_timeout") from exc
                time.sleep(_INTERPROCESS_LOCK_POLL_SECONDS)

    def __exit__(self, *_exc_info: object) -> None:
        if self._handle is None:
            return
        try:
            _unlock_file(self._handle)
        finally:
            self._handle.close()
            self._handle = None


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

        lock = _thread_lock(self.vault_root, conversation.thread_id)
        process_lock = self._runtime_path(
            ".locks/"
            f"thread-{_vault_identity(self.vault_root)}-{conversation.thread_id}.lock"
        )
        with lock, _InterProcessFileLock(process_lock):
            return self._save_locked(
                conversation=conversation,
                participant_resolver=participant_resolver,
                summary=summary,
                meeting_run=meeting_run,
            )

    def _save_locked(
        self,
        *,
        conversation: DiscordConversation,
        participant_resolver: ParticipantResolver,
        summary: ConversationSummary,
        meeting_run: MeetingRun | None,
    ) -> ObsidianSaveResult:
        """Run one same-thread save while its process-local lock is held."""

        messages = conversation.messages
        first_message_id = min(
            messages, key=lambda item: int(item.message_id)
        ).message_id
        latest_message_id = max(
            messages, key=lambda item: int(item.message_id)
        ).message_id
        classification = "meeting" if meeting_run is not None else "conversation"
        one_line_summary = _safe(summary.summary)
        evidence_hash = _evidence_hash(conversation, classification, meeting_run)
        checkpoint_relative = f"{conversation.thread_id}.json"
        checkpoint_path = self._runtime_path(checkpoint_relative)
        checkpoint = self._load_checkpoint(checkpoint_path, conversation.thread_id)
        obsolete_canonical_relative = None

        if checkpoint is not None:
            previous_latest = str(checkpoint["latest_message_id"])
            canonical_relative = str(checkpoint["canonical_path"])
            snapshot_paths = [str(path) for path in checkpoint["snapshot_paths"]]
            self._validate_checkpoint_evidence(
                conversation,
                snapshot_paths,
                classification=classification,
                meeting_run=meeting_run,
                validate_latest=previous_latest == latest_message_id,
            )
            if int(latest_message_id) < int(previous_latest):
                return ObsidianSaveResult(
                    status="unchanged",
                    classification=classification,
                    new_message_count=0,
                    snapshot_path=snapshot_paths[-1],
                    canonical_path=canonical_relative,
                    one_line_summary=one_line_summary,
                )
            snapshot_paths = self._merge_snapshot_paths(
                conversation.thread_id,
                snapshot_paths,
                self._discover_orphan_snapshots(
                    conversation.thread_id, latest_message_id
                ),
            )
            recovered_canonical = _canonical_relative_from_snapshot(snapshot_paths[0])
            if recovered_canonical != canonical_relative:
                obsolete_canonical_relative = canonical_relative
                canonical_relative = recovered_canonical
            if latest_message_id == previous_latest:
                self._write_mutable_state(
                    conversation=conversation,
                    participants=_resolve_participants(
                        conversation, participant_resolver
                    ),
                    summary=summary,
                    classification=classification,
                    meeting_run=meeting_run,
                    canonical_relative=canonical_relative,
                    snapshot_paths=snapshot_paths,
                    latest_message_id=latest_message_id,
                    saved_at=_now_iso(),
                    checkpoint_relative=checkpoint_relative,
                    obsolete_canonical_relative=obsolete_canonical_relative,
                )
                return ObsidianSaveResult(
                    status="unchanged",
                    classification=classification,
                    new_message_count=0,
                    snapshot_path=snapshot_paths[-1],
                    canonical_path=canonical_relative,
                    one_line_summary=one_line_summary,
                )
            status = "updated"
            new_message_count = sum(
                int(message.message_id) > int(previous_latest) for message in messages
            )
        else:
            status = "created"
            new_message_count = len(messages)
            snapshot_paths = self._discover_orphan_snapshots(
                conversation.thread_id, latest_message_id
            )
            canonical_relative = (
                _canonical_relative_from_snapshot(snapshot_paths[0])
                if snapshot_paths
                else self._canonical_relative_path(conversation)
            )

        snapshot_relative = next(
            (
                relative
                for relative in snapshot_paths
                if _snapshot_id_from_relative(relative, conversation.thread_id)
                == latest_message_id
            ),
            None,
        )
        if snapshot_relative is None:
            snapshot_relative = self._snapshot_relative_path(
                conversation, latest_message_id
            )
        snapshot_path = _contained_path(self.vault_root, snapshot_relative)

        participants = _resolve_participants(conversation, participant_resolver)
        saved_at = _now_iso()
        if snapshot_path.exists():
            expected_evidence_hash = _snapshot_evidence_hash_for_conversation(
                snapshot_path,
                conversation=conversation,
                classification=classification,
                meeting_run=meeting_run,
            )
            _validate_snapshot(
                snapshot_path,
                thread_id=conversation.thread_id,
                latest_message_id=latest_message_id,
                expected_evidence_hash=expected_evidence_hash,
            )
        else:
            raw_markdown = _render_raw_snapshot(
                conversation=conversation,
                participants=participants,
                classification=classification,
                meeting_run=meeting_run,
                saved_at=saved_at,
                first_message_id=first_message_id,
                latest_message_id=latest_message_id,
                evidence_hash=evidence_hash,
            )
            self._write_raw_exclusive(snapshot_path, raw_markdown)

        all_snapshot_paths = self._merge_snapshot_paths(
            conversation.thread_id, snapshot_paths, [snapshot_relative]
        )
        self._write_mutable_state(
            conversation=conversation,
            participants=participants,
            summary=summary,
            classification=classification,
            meeting_run=meeting_run,
            canonical_relative=canonical_relative,
            snapshot_paths=all_snapshot_paths,
            latest_message_id=latest_message_id,
            saved_at=saved_at,
            checkpoint_relative=checkpoint_relative,
            obsolete_canonical_relative=obsolete_canonical_relative,
        )
        return ObsidianSaveResult(
            status=status,
            classification=classification,
            new_message_count=new_message_count,
            snapshot_path=snapshot_relative,
            canonical_path=canonical_relative,
            one_line_summary=one_line_summary,
        )

    def _write_mutable_state(
        self,
        *,
        conversation: DiscordConversation,
        participants: tuple[ParticipantIdentity, ...],
        summary: ConversationSummary,
        classification: str,
        meeting_run: MeetingRun | None,
        canonical_relative: str,
        snapshot_paths: list[str],
        latest_message_id: str,
        saved_at: str,
        checkpoint_relative: str,
        obsolete_canonical_relative: str | None,
    ) -> None:
        canonical_path = _contained_path(self.vault_root, canonical_relative)
        canonical_markdown = _render_canonical_page(
            conversation=conversation,
            participants=participants,
            summary=summary,
            classification=classification,
            meeting_run=meeting_run,
            snapshot_paths=snapshot_paths,
            artifact_paths=self._meeting_artifact_paths(meeting_run),
        )
        _atomic_write_text(canonical_path, canonical_markdown)
        if obsolete_canonical_relative is not None:
            _contained_path(self.vault_root, obsolete_canonical_relative).unlink(
                missing_ok=True
            )
        shared_process_lock = self._runtime_path(
            f".locks/vault-{_vault_identity(self.vault_root)}.lock"
        )
        with _vault_lock(self.vault_root), _InterProcessFileLock(shared_process_lock):
            self._update_log(
                conversation=conversation,
                canonical_relative=canonical_relative,
                one_line_summary=_safe(summary.summary),
                latest_message_id=latest_message_id,
                saved_at=saved_at,
            )
            self._reconcile_index(
                conversation=conversation,
                canonical_relative=canonical_relative,
                one_line_summary=_safe(summary.summary),
                meeting_run=meeting_run,
                include=meeting_run is not None or summary.important,
            )

        _atomic_write_json(
            self._runtime_path(checkpoint_relative),
            {
                "thread_id": conversation.thread_id,
                "latest_message_id": latest_message_id,
                "canonical_path": canonical_relative,
                "snapshot_paths": snapshot_paths,
            },
        )

    def _runtime_path(self, relative: str) -> Path:
        return _contained_path(self.workspace_root, f"runtime/discord_save/{relative}")

    def _discover_orphan_snapshots(
        self, thread_id: str, latest_message_id: str
    ) -> list[str]:
        raw_dir = _contained_path(self.vault_root, "raw/chat-logs")
        if not raw_dir.exists():
            return []
        discovered = []
        for path in raw_dir.glob(f"*__{thread_id}__*.md"):
            relative = PurePosixPath("raw", "chat-logs", path.name).as_posix()
            orphan_id = _snapshot_id_from_relative(relative, thread_id)
            if int(orphan_id) > int(latest_message_id):
                continue
            _validate_snapshot(
                _contained_path(self.vault_root, relative),
                thread_id=thread_id,
                latest_message_id=orphan_id,
                expected_evidence_hash=None,
            )
            discovered.append(relative)
        return self._merge_snapshot_paths(thread_id, discovered)

    def _merge_snapshot_paths(
        self, thread_id: str, *path_groups: Iterable[str]
    ) -> list[str]:
        by_latest_id: dict[str, str] = {}
        for relative in (path for group in path_groups for path in group):
            latest_id = _snapshot_id_from_relative(relative, thread_id)
            existing = by_latest_id.get(latest_id)
            if existing is not None and existing != relative:
                raise ValueError("ambiguous_immutable_snapshot")
            by_latest_id[latest_id] = relative
        return [by_latest_id[latest_id] for latest_id in sorted(by_latest_id, key=int)]

    def _validate_checkpoint_evidence(
        self,
        conversation: DiscordConversation,
        snapshot_paths: list[str],
        *,
        classification: str,
        meeting_run: MeetingRun | None,
        validate_latest: bool,
    ) -> None:
        for index, relative in enumerate(snapshot_paths):
            latest_id = _snapshot_id_from_relative(relative, conversation.thread_id)
            snapshot_path = _contained_path(self.vault_root, relative)
            expected_evidence_hash = None
            if (
                validate_latest
                and index == len(snapshot_paths) - 1
                and snapshot_path.exists()
            ):
                expected_evidence_hash = _snapshot_evidence_hash_for_conversation(
                    snapshot_path,
                    conversation=conversation,
                    classification=classification,
                    meeting_run=meeting_run,
                )
            _validate_snapshot(
                snapshot_path,
                thread_id=conversation.thread_id,
                latest_message_id=latest_id,
                expected_evidence_hash=expected_evidence_hash,
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
            if not isinstance(payload["latest_message_id"], str):
                raise TypeError
            _validate_snowflake(payload["latest_message_id"], "latest_message_id")
            if not isinstance(payload["canonical_path"], str):
                raise TypeError
            if not isinstance(payload["snapshot_paths"], list) or not all(
                isinstance(value, str) for value in payload["snapshot_paths"]
            ):
                raise TypeError
            _validate_checkpoint_namespace(
                payload,
                expected_thread_id,
                self.vault_root,
            )
            return payload
        except (
            json.JSONDecodeError,
            KeyError,
            OSError,
            TypeError,
            UnicodeError,
            ValueError,
        ) as exc:
            raise ValueError("invalid_checkpoint") from exc

    def _write_raw_exclusive(self, path: Path, markdown: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        created = False
        try:
            with path.open("x", encoding="utf-8", newline="\n") as handle:
                created = True
                handle.write(markdown)
                handle.flush()
                os.fsync(handle.fileno())
        except Exception:
            if created:
                path.unlink(missing_ok=True)
            raise

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
        existing = path.read_text(encoding="utf-8") if path.exists() else "# Log\n"
        if any(
            (match := _LOG_MARKER_RE.search(line))
            and match.groups() == (conversation.thread_id, latest_message_id)
            for line in existing.splitlines()
        ):
            return
        line = (
            f"- {_md_inline(saved_at)} [{_md_inline(conversation.thread_name)}]"
            f"({_md_link_destination(canonical_relative)}) - "
            f"{_md_inline(one_line_summary)} "
            f"<!-- oracle-log:{conversation.thread_id}:{latest_message_id} -->"
        )
        _atomic_write_text(path, existing.rstrip() + "\n" + line + "\n")

    def _reconcile_index(
        self,
        *,
        conversation: DiscordConversation,
        canonical_relative: str,
        one_line_summary: str,
        meeting_run: MeetingRun | None,
        include: bool,
    ) -> None:
        path = _contained_path(self.vault_root, "wiki/index.md")
        if not path.exists() and not include:
            return
        existing = path.read_text(encoding="utf-8") if path.exists() else "# Index\n"
        meeting_text = (
            f" meeting={_md_inline(meeting_run.meeting_run_id)}" if meeting_run else ""
        )
        line = (
            f"- [{_md_inline(conversation.thread_name)}]"
            f"({_md_link_destination(canonical_relative)})"
            f" - {_md_inline(one_line_summary)}{meeting_text} "
            f"<!-- oracle-index:{conversation.thread_id} -->"
        )
        lines = []
        for existing_line in existing.rstrip().splitlines():
            marker = _INDEX_MARKER_RE.search(existing_line)
            if marker and marker.group(1) == conversation.thread_id:
                continue
            lines.append(existing_line)
        if include:
            lines.append(line)
        _atomic_write_text(path, "\n".join(lines) + "\n")

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
            resolved_run_dir = _resolved_directory_inside(run_dir, self.workspace_root)
            if resolved_run_dir is not None:
                paths.extend(
                    relative
                    for path in sorted(resolved_run_dir.rglob("*"))
                    if (relative := _artifact_relative_path(path, self.workspace_root))
                )
        return tuple(dict.fromkeys(_safe(path) for path in paths))


def _lock_path_key(path: Path) -> str:
    return _resolved_path_key(path)


def _vault_identity(vault_root: Path) -> str:
    return hashlib.sha256(_lock_path_key(vault_root).encode("utf-8")).hexdigest()[:24]


def _lock_file_nonblocking(handle: Any) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_file(handle: Any) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _lock_is_busy(exc: OSError) -> bool:
    return exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK} or getattr(
        exc, "winerror", None
    ) in {33, 36}


def _resolved_path_key(path: Path) -> str:
    resolved = str(path.resolve())
    if resolved.startswith("\\\\?\\UNC\\"):
        resolved = "\\\\" + resolved[8:]
    elif resolved.startswith("\\\\?\\"):
        resolved = resolved[4:]
    return os.path.normcase(os.path.normpath(resolved))


def _thread_lock(vault_root: Path, thread_id: str) -> threading.RLock:
    key = (_lock_path_key(vault_root), thread_id)
    with _LOCKS_GUARD:
        return _THREAD_LOCKS.setdefault(key, threading.RLock())


def _vault_lock(vault_root: Path) -> threading.RLock:
    key = _lock_path_key(vault_root)
    with _LOCKS_GUARD:
        return _VAULT_LOCKS.setdefault(key, threading.RLock())


def _validate_checkpoint_namespace(
    payload: Mapping[str, object], thread_id: str, vault_root: Path
) -> None:
    canonical = str(payload["canonical_path"])
    snapshots = [str(value) for value in payload["snapshot_paths"]]
    latest_id = str(payload["latest_message_id"])
    if not snapshots:
        raise ValueError("checkpoint has no snapshots")

    canonical_pure = PurePosixPath(canonical)
    if (
        len(canonical_pure.parts) != 3
        or canonical_pure.parts[:2] != ("wiki", "conversations")
        or canonical_pure.suffix != ".md"
    ):
        raise ValueError("invalid canonical namespace")
    canonical_pattern = re.compile(
        rf"^\d{{4}}-\d{{2}}-\d{{2}}_.+__{re.escape(thread_id)}\.md$"
    )
    if not canonical_pattern.fullmatch(canonical_pure.name):
        raise ValueError("invalid canonical identity")
    _contained_path(vault_root, canonical)

    snapshot_ids = []
    for relative in snapshots:
        snapshot_pure = PurePosixPath(relative)
        if (
            len(snapshot_pure.parts) != 3
            or snapshot_pure.parts[:2] != ("raw", "chat-logs")
            or snapshot_pure.suffix != ".md"
        ):
            raise ValueError("invalid snapshot namespace")
        snapshot_ids.append(_snapshot_id_from_relative(relative, thread_id))
        _contained_path(vault_root, relative)
    if len(set(snapshots)) != len(snapshots):
        raise ValueError("duplicate snapshot path")
    if any(
        int(current) <= int(previous)
        for previous, current in zip(snapshot_ids, snapshot_ids[1:], strict=False)
    ):
        raise ValueError("non-monotonic snapshot paths")
    if snapshot_ids[-1] != latest_id:
        raise ValueError("checkpoint latest snapshot mismatch")
    first_snapshot_stem = PurePosixPath(snapshots[0]).stem.rsplit("__", 1)[0]
    if canonical_pure.stem != first_snapshot_stem:
        raise ValueError("canonical does not match first snapshot")


def _snapshot_id_from_relative(relative: str, thread_id: str) -> str:
    pure = PurePosixPath(relative)
    pattern = re.compile(
        rf"^\d{{4}}-\d{{2}}-\d{{2}}_.+__{re.escape(thread_id)}"
        r"__([0-9]{1,24})\.md$"
    )
    match = pattern.fullmatch(pure.name)
    if match is None:
        raise ValueError("invalid snapshot identity")
    return match.group(1)


def _canonical_relative_from_snapshot(snapshot_relative: str) -> str:
    snapshot = PurePosixPath(snapshot_relative)
    stable_stem = snapshot.stem.rsplit("__", 1)[0]
    return f"wiki/conversations/{stable_stem}.md"


def _evidence_hash(
    conversation: DiscordConversation,
    classification: str,
    meeting_run: MeetingRun | None,
    *,
    thread_name: str | None = None,
) -> str:
    payload = {
        "classification": classification,
        "meeting_run_id": meeting_run.meeting_run_id if meeting_run else "",
        "conversation": {
            "guild_id": conversation.guild_id,
            "parent_channel_id": conversation.parent_channel_id,
            "thread_id": conversation.thread_id,
            "thread_name": (
                conversation.thread_name if thread_name is None else thread_name
            ),
            "visibility": conversation.visibility,
            "messages": [
                {
                    "message_id": message.message_id,
                    "created_at": message.created_at,
                    "content": message.content,
                    "author": {
                        "user_id": message.author.user_id,
                        "display_name": message.author.display_name,
                        "bot": message.author.bot,
                    },
                    "attachments": [
                        {
                            "attachment_id": attachment.attachment_id,
                            "filename": attachment.filename,
                            "content_type": attachment.content_type,
                            "size": attachment.size,
                            "url": attachment.url,
                        }
                        for attachment in message.attachments
                    ],
                }
                for message in conversation.messages
            ],
        },
    }
    encoded = json.dumps(
        _sanitize_json(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _snapshot_evidence_hash_for_conversation(
    path: Path,
    *,
    conversation: DiscordConversation,
    classification: str,
    meeting_run: MeetingRun | None,
) -> str:
    try:
        stored_thread_name = _read_frontmatter(path)["discord_thread_name"]
        if not isinstance(stored_thread_name, str):
            raise TypeError
    except (
        KeyError,
        OSError,
        TypeError,
        UnicodeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        raise ValueError("invalid_immutable_snapshot") from exc
    return _evidence_hash(
        conversation,
        classification,
        meeting_run,
        thread_name=stored_thread_name,
    )


def _validate_snapshot(
    path: Path,
    *,
    thread_id: str,
    latest_message_id: str,
    expected_evidence_hash: str | None,
) -> None:
    if not path.exists():
        raise FileNotFoundError("missing_immutable_snapshot")
    if not path.is_file():
        raise ValueError("invalid_immutable_snapshot")
    try:
        frontmatter = _read_frontmatter(path)
        evidence_hash = frontmatter["evidence_sha256"]
        document_hash = frontmatter["document_sha256"]
        if (
            frontmatter["discord_thread_id"] != thread_id
            or frontmatter["latest_message_id"] != latest_message_id
            or not isinstance(evidence_hash, str)
            or not _EVIDENCE_HASH_RE.fullmatch(evidence_hash)
            or not isinstance(document_hash, str)
            or not _EVIDENCE_HASH_RE.fullmatch(document_hash)
            or document_hash != _snapshot_document_hash(path)
            or (
                expected_evidence_hash is not None
                and evidence_hash != expected_evidence_hash
            )
        ):
            raise ValueError
    except (
        KeyError,
        OSError,
        TypeError,
        UnicodeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        raise ValueError("invalid_immutable_snapshot") from exc


def _snapshot_document_hash(path: Path) -> str:
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    unsigned_lines = [
        line for line in lines if not line.startswith("document_sha256: ")
    ]
    if len(unsigned_lines) != len(lines) - 1:
        raise ValueError("invalid document hash field")
    return hashlib.sha256("".join(unsigned_lines).encode("utf-8")).hexdigest()


def _read_frontmatter(path: Path) -> dict[str, object]:
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "---":
        raise ValueError("missing frontmatter")
    result: dict[str, object] = {}
    for line in lines[1:]:
        if line == "---":
            return result
        if line.startswith(" ") or ": " not in line:
            continue
        key, raw_value = line.split(": ", 1)
        result[key] = json.loads(raw_value)
    raise ValueError("unterminated frontmatter")


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
    evidence_hash: str,
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
        f"evidence_sha256: {_yaml_value(evidence_hash)}",
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
            f"- {_md_inline(message.created_at)} {_md_inline(speaker)}: "
            f"{_md_inline(message.content)} "
            f"(message ID: `{message.message_id}`)"
        )
    urls = _conversation_urls(conversation)
    attachments = [
        attachment
        for message in conversation.messages
        for attachment in message.attachments
    ]
    body = [
        f"# {_md_inline(conversation.thread_name)} raw snapshot",
        "",
        "## Source",
        f"- [{_md_inline(conversation.thread_name)}]({source_url})",
        f"- Message range: `{first_message_id}` to `{latest_message_id}`",
        "",
        "## Participants",
        *[f"- {participant}" for participant in participant_body],
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
    unsigned = "\n".join([*frontmatter, "", *body]) + "\n"
    document_hash = hashlib.sha256(unsigned.encode("utf-8")).hexdigest()
    hash_index = frontmatter.index(f"evidence_sha256: {_yaml_value(evidence_hash)}") + 1
    frontmatter.insert(
        hash_index,
        f"document_sha256: {_yaml_value(document_hash)}",
    )
    return "\n".join([*frontmatter, "", *body]) + "\n"


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
    frontmatter = [
        "---",
        f"type: {_yaml_value(classification)}",
        f"discord_guild_id: {_yaml_value(conversation.guild_id)}",
        f"discord_parent_channel_id: {_yaml_value(conversation.parent_channel_id)}",
        f"discord_thread_id: {_yaml_value(conversation.thread_id)}",
        f"discord_thread_name: {_yaml_value(conversation.thread_name)}",
        f"visibility: {_yaml_value(conversation.visibility)}",
        "meeting_run_id: "
        + _yaml_value(meeting_run.meeting_run_id if meeting_run else ""),
        "---",
    ]
    sections = [
        *frontmatter,
        "",
        f"# {_md_inline(conversation.thread_name)}",
        "",
        "## Source",
        f"- Type: {_md_inline(classification)}",
        f"- Discord: [{_md_inline(conversation.thread_name)}]({source_url})",
        f"- Thread ID: `{conversation.thread_id}`",
        "",
        "## One-line summary",
        _md_inline(summary.summary) or "None.",
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
        *[f"- {_participant_markdown(participant)}" for participant in participants],
        "",
        "## User perspective",
        _md_inline(summary.user_perspective) or "None.",
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
            *[f"- [[{path}]]" for path in snapshot_paths],
        ]
    )
    return "\n".join(sections) + "\n"


def _participant_markdown(participant: ParticipantIdentity) -> str:
    label = participant.role or participant.discord_name
    profile = (
        f", hermes_profile: {_md_inline(participant.hermes_profile)}"
        if participant.hermes_profile
        else ""
    )
    role = f" (role: {_md_inline(participant.role)})" if participant.role else ""
    return (
        f"{_md_inline(label)}{role} - "
        f"discord_name: {_md_inline(participant.discord_name)}, "
        f"discord_user_id: {_md_inline(participant.discord_user_id)}{profile}"
    )


def _action_item_markdown(summary: ConversationSummary) -> list[str]:
    if not summary.action_items:
        return ["- None."]
    return [
        f"- {_md_inline(item.text)}"
        + (f" (owner: {_md_inline(item.owner)})" if item.owner else "")
        for item in summary.action_items
    ]


def _meeting_evidence_markdown(
    meeting_run: MeetingRun, artifact_paths: tuple[str, ...]
) -> list[str]:
    state = getattr(meeting_run.state, "value", str(meeting_run.state))
    lines = [
        f"- MeetingRun ID: `{_md_inline(meeting_run.meeting_run_id)}`",
        f"- State: `{_md_inline(state)}`",
    ]
    agenda = str(meeting_run.trigger.get("text") or "")
    if agenda:
        lines.append(f"- Agenda: {_md_inline(agenda)}")
    id_groups = (
        ("Worker task IDs", meeting_run.worker_task_ids),
        ("Validation IDs", meeting_run.validation_ids),
        ("Projection event IDs", meeting_run.projection_event_ids),
        ("Checkpoint IDs", meeting_run.checkpoint_ids),
    )
    for label, values in id_groups:
        if values:
            lines.append(
                f"- {label}: " + ", ".join(f"`{_md_inline(value)}`" for value in values)
            )
    for key, value in sorted(meeting_run.hermes_refs.items()):
        if value:
            lines.append(f"- Hermes {_md_inline(key)}: `{_md_inline(value)}`")
    for key in ("created_at", "started_at", "updated_at", "completed_at"):
        value = meeting_run.metadata.get(key)
        if value:
            lines.append(f"- {_md_inline(key)}: `{_md_inline(value)}`")
    lines.extend(
        f"- Artifact: [{_md_inline(path)}]({_md_link_destination(path)})"
        for path in artifact_paths
    )
    return lines


def _conversation_urls(conversation: DiscordConversation) -> tuple[str, ...]:
    urls = []
    for message in conversation.messages:
        for match in _URL_RE.findall(message.content):
            url, _trailing = _split_url_trailing(match)
            urls.append(_redact_url(url))
    return tuple(dict.fromkeys(_safe(url) for url in urls if url))


def _attachment_markdown(attachments: Iterable[DiscordAttachment]) -> list[str]:
    rendered = []
    for attachment in attachments:
        rendered.extend(
            (
                f"- attachment_id: {_md_inline(attachment.attachment_id)}",
                f"  filename: {_md_inline(attachment.filename)}",
                f"  content_type: {_md_inline(attachment.content_type)}",
                f"  size: {_md_inline(attachment.size)}",
                f"  url: {_md_inline(attachment.url)}",
            )
        )
    return rendered or ["- None."]


def _markdown_items(
    values: Iterable[object], *, already_list: bool = False
) -> list[str]:
    if already_list:
        rendered = [str(value) for value in values if str(value)]
        return rendered or ["- None."]
    safe_values = [_md_inline(value) for value in values if _safe(value)]
    if not safe_values:
        return ["- None."]
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
    if not path.is_absolute():
        pure = PurePosixPath(raw)
        if (
            not raw
            or pure.is_absolute()
            or any(part in {"", ".", ".."} for part in pure.parts)
        ):
            return ""
        path = workspace_root.joinpath(*pure.parts)
    try:
        resolved = path.resolve(strict=True)
        relative = resolved.relative_to(workspace_root.resolve())
    except (FileNotFoundError, OSError, ValueError):
        return ""
    return relative.as_posix() if resolved.is_file() else ""


def _resolved_directory_inside(path: Path, workspace_root: Path) -> Path | None:
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(workspace_root.resolve())
    except (FileNotFoundError, OSError, ValueError):
        return None
    return resolved if resolved.is_dir() else None


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
    candidate = root.joinpath(*pure.parts)
    try:
        root_resolved = _resolved_path_key(root)
        candidate_resolved = _resolved_path_key(candidate)
        if os.path.commonpath((root_resolved, candidate_resolved)) != root_resolved:
            raise ValueError("resolved path is outside root")
    except (OSError, ValueError) as exc:
        raise ValueError("path escapes root") from exc
    return candidate


def _yaml_value(value: object) -> str:
    return json.dumps(_safe(value), ensure_ascii=False)


def _safe(value: object) -> str:
    text = _sanitize_text(str(value))
    return "".join(
        character
        for character in text
        if character in {"\n", "\t"} or (ord(character) >= 32 and ord(character) != 127)
    )


def _sanitize_text(text: str) -> str:
    urls: list[str] = []

    def protect_url(match: re.Match[str]) -> str:
        url, trailing = _split_url_trailing(match.group(0))
        token = f"ORACLEURLPLACEHOLDER{len(urls)}END"
        urls.append(_redact_url(url) + trailing)
        return token

    protected = _URL_RE.sub(protect_url, text)
    sanitized = sanitize_knowledge_text(protected)
    for index, url in enumerate(urls):
        sanitized = sanitized.replace(f"ORACLEURLPLACEHOLDER{index}END", url)
    return sanitized


def _split_url_trailing(value: str) -> tuple[str, str]:
    url = value.rstrip(".,;:!?)")
    return url, value[len(url) :]


def _redact_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return sanitize_knowledge_text(value)
    query = _redact_url_parameters(parsed.query)
    fragment = (
        _redact_url_parameters(parsed.fragment)
        if "=" in parsed.fragment
        else sanitize_knowledge_text(parsed.fragment)
    )
    return urlunsplit(
        (
            sanitize_knowledge_text(parsed.scheme),
            sanitize_knowledge_text(parsed.netloc),
            sanitize_knowledge_text(parsed.path),
            query,
            fragment,
        )
    )


def _redact_url_parameters(value: str) -> str:
    pairs = parse_qsl(value, keep_blank_values=True)
    sanitized_pairs = []
    for key, parameter_value in pairs:
        safe_key = sanitize_knowledge_text(key)
        safe_value = (
            "[REDACTED_SECRET]"
            if _is_secret_url_key(key)
            else sanitize_knowledge_text(parameter_value)
        )
        sanitized_pairs.append((safe_key, safe_value))
    return urlencode(sanitized_pairs, doseq=True, safe="[]/:+")


def _is_secret_url_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", key.casefold()).strip("_")
    return (
        normalized in _SECRET_URL_KEYS
        or normalized.endswith(("_auth", "_key", "_sig", "_signature", "_token"))
        or any(
            marker in normalized
            for marker in ("password", "passwd", "pwd", "secret", "credential")
        )
    )


def _md_inline(value: object) -> str:
    text = _safe(value).replace("\r\n", "\n").replace("\r", "\n")
    markers = ("[REDACTED_SECRET]", "@[redacted-mention]")
    parts = re.split(
        "(" + "|".join(re.escape(marker) for marker in markers) + ")",
        text,
    )
    rendered = []
    for part in parts:
        if part in markers:
            rendered.append(part)
            continue
        escaped = html.escape(part, quote=False)
        escaped = re.sub(r"([\\`*_{}\[\]!|])", r"\\\1", escaped)
        rendered.append(" <br> ".join(escaped.split("\n")))
    return "".join(rendered)


def _md_link_destination(value: str) -> str:
    return quote(_safe(value), safe="/:?#&=%[]@+,-._~")


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
