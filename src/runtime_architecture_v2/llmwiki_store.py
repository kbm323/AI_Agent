"""Immutable LLM Wiki records with searchable canonical Markdown pages."""

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
from collections.abc import Mapping, Sequence
from contextlib import suppress
from pathlib import Path, PurePosixPath
from typing import Any

from .knowledge import sanitize_knowledge_text, sanitize_url
from .llmwiki_models import LlmWikiSource, LlmWikiSummary, LlmWikiWriteResult

_LOCK_TIMEOUT_SECONDS = 30.0
_LOCK_POLL_SECONDS = 0.02
_LOCKS_GUARD = threading.Lock()
_VAULT_LOCKS: dict[str, threading.RLock] = {}


class _InterProcessFileLock:
    """Bounded kernel lock stored outside the synced vault."""

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
        deadline = time.monotonic() + _LOCK_TIMEOUT_SECONDS
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
                time.sleep(_LOCK_POLL_SECONDS)

    def __exit__(self, *_exc_info: object) -> None:
        if self._handle is None:
            return
        try:
            _unlock_file(self._handle)
        finally:
            self._handle.close()
            self._handle = None


class LlmWikiStore:
    """Write raw evidence once and keep curated pages current for one vault."""

    def __init__(self, *, vault_root: str | Path, runtime_root: str | Path) -> None:
        self.vault_root = Path(vault_root)
        self.workspace_root = Path(runtime_root)
        if _is_inside(self.workspace_root, self.vault_root):
            raise ValueError("runtime_root_inside_vault")
        self._lock = _vault_lock(self.vault_root)

    def save_note(self, text: str, *, author: str) -> LlmWikiWriteResult:
        safe_text = _clean(text)
        safe_author = _clean(author)
        if not safe_text.strip() or not safe_author.strip():
            return _failure("invalid_input")
        note_id = _digest(f"{safe_author}\n{safe_text}")[:24]
        record_id = f"note-{note_id}"
        raw_path = f"raw/notes/{note_id}.md"
        canonical_path = f"wiki/notes/{note_id}.md"
        try:
            with self._lock, _InterProcessFileLock(self._lock_path()):
                raw = _contained_path(self.vault_root, raw_path)
                canonical = _contained_path(self.vault_root, canonical_path)
                log = _contained_path(self.vault_root, "wiki/log.md")
                raw_exists = raw.exists()
                canonical_text = _render_note_canonical(
                    record_id, raw_path, safe_author, safe_text
                )
                needs_canonical = not _text_matches(canonical, canonical_text)
                needs_log = not _has_marker(log, _log_marker(record_id))
                if not raw_exists:
                    _write_raw_exclusive(
                        raw, _render_note_raw(record_id, safe_author, safe_text)
                    )
                if needs_canonical:
                    _atomic_write_text(canonical, canonical_text)
                if needs_log:
                    _append_once(
                        log,
                        _render_log_entry("note", raw_path, canonical_path),
                        _log_marker(record_id),
                    )
                status = "created" if not raw_exists else "updated"
                if raw_exists and not needs_canonical and not needs_log:
                    status = "unchanged"
                return _result(status, record_id, raw_path, canonical_path)
        except (OSError, TimeoutError):
            return _failure("write_failed")
        except ValueError:
            return _failure("unsafe_path")

    def save_source(
        self, source: LlmWikiSource, summary: LlmWikiSummary
    ) -> LlmWikiWriteResult:
        safe_url = sanitize_url(_clean(source.normalized_url).strip())
        safe_content = _clean(source.content)
        if not safe_url or not safe_content.strip():
            return _failure("invalid_input")
        source_id = _digest(safe_url)[:24]
        snapshot_id = _digest(f"{safe_url}\n{safe_content}")[:24]
        record_id = f"source-{source_id}-{snapshot_id}"
        raw_path = f"raw/sources/{source_id}__{snapshot_id}.md"
        canonical_path = f"wiki/sources/{source_id}.md"
        try:
            with self._lock, _InterProcessFileLock(self._lock_path()):
                raw = _contained_path(self.vault_root, raw_path)
                canonical = _contained_path(self.vault_root, canonical_path)
                log = _contained_path(self.vault_root, "wiki/log.md")
                index = _contained_path(self.vault_root, "wiki/index.md")
                raw_exists = raw.exists()
                has_prior_snapshot = self._has_source_snapshot(source_id)
                if not raw_exists:
                    _write_raw_exclusive(
                        raw,
                        _render_source_raw(
                            record_id=record_id,
                            source=source,
                            safe_url=safe_url,
                            safe_content=safe_content,
                        ),
                    )
                snapshots = self._source_snapshot_paths(source_id)
                canonical_text = _render_source_canonical(
                    source=source,
                    summary=summary,
                    safe_url=safe_url,
                    snapshots=snapshots,
                )
                needs_canonical = not _text_matches(canonical, canonical_text)
                needs_log = not _has_marker(log, _log_marker(record_id))
                needs_index = not _has_marker(index, _index_marker(record_id))
                if needs_canonical:
                    _atomic_write_text(canonical, canonical_text)
                if needs_log:
                    _append_once(
                        log,
                        _render_log_entry("source", raw_path, canonical_path),
                        _log_marker(record_id),
                    )
                if needs_index:
                    _append_once(
                        index,
                        _render_index_entry(summary, source.title, canonical_path),
                        _index_marker(record_id),
                    )
                status = "created" if not has_prior_snapshot else "updated"
                if (
                    raw_exists
                    and not needs_canonical
                    and not needs_log
                    and not needs_index
                ):
                    status = "unchanged"
                return _result(status, record_id, raw_path, canonical_path)
        except (OSError, TimeoutError):
            return _failure("write_failed")
        except ValueError:
            return _failure("unsafe_path")

    def _lock_path(self) -> Path:
        vault_id = _digest(_resolved_path_key(self.vault_root))[:24]
        return _runtime_path(
            self.workspace_root, f".locks/vault-{vault_id}.lock"
        )

    def _has_source_snapshot(self, source_id: str) -> bool:
        raw_directory = _contained_path(self.vault_root, "raw/sources")
        return any(raw_directory.glob(f"{source_id}__*.md"))

    def _source_snapshot_paths(self, source_id: str) -> tuple[str, ...]:
        raw_directory = _contained_path(self.vault_root, "raw/sources")
        paths = []
        for path in sorted(raw_directory.glob(f"{source_id}__*.md")):
            relative = path.relative_to(self.vault_root).as_posix()
            _contained_path(self.vault_root, relative)
            paths.append(relative)
        return tuple(paths)


def _result(
    status: str, record_id: str, raw_path: str, canonical_path: str
) -> LlmWikiWriteResult:
    return LlmWikiWriteResult(
        ok=True,
        status=status,
        record_id=record_id,
        raw_path=raw_path,
        canonical_path=canonical_path,
    )


def _failure(error: str) -> LlmWikiWriteResult:
    return LlmWikiWriteResult(ok=False, status="failed", error=error)


def _render_note_raw(record_id: str, author: str, text: str) -> str:
    return (
        f"# Note {record_id}\n\n"
        f"Author: {_inline(author)}\n\n"
        "## Content\n\n"
        f"{_quoted(text)}\n"
    )


def _render_note_canonical(
    record_id: str, raw_path: str, author: str, text: str
) -> str:
    return (
        f"# Note by {_inline(author)}\n\n"
        f"Record: `{record_id}`\n\n"
        f"Raw record: [{raw_path}]({raw_path})\n\n"
        "## Note\n\n"
        f"{_quoted(text)}\n"
    )


def _render_source_raw(
    *,
    record_id: str,
    source: LlmWikiSource,
    safe_url: str,
    safe_content: str,
) -> str:
    metadata = json.dumps(
        _json_safe(source.metadata), ensure_ascii=False, sort_keys=True
    )
    return (
        f"# Source {_inline(source.title)}\n\n"
        f"Record: `{record_id}`\n\n"
        f"URL: {_inline(safe_url)}\n\n"
        f"Type: {_inline(source.source_type)}\n\n"
        f"Retrieved at: {_inline(source.retrieved_at)}\n\n"
        f"Metadata: `{_inline(metadata)}`\n\n"
        "## Content\n\n"
        f"{_quoted(safe_content)}\n"
    )


def _render_source_canonical(
    *,
    source: LlmWikiSource,
    summary: LlmWikiSummary,
    safe_url: str,
    snapshots: tuple[str, ...],
) -> str:
    snapshot_lines = "\n".join(f"- [{path}]({path})" for path in snapshots)
    points = "\n".join(f"- {_inline(point)}" for point in summary.key_points)
    tags = ", ".join(_inline(tag) for tag in summary.tags)
    sections = [
        f"# {_inline(summary.title or source.title)}",
        f"URL: {_inline(safe_url)}",
        f"Type: {_inline(source.source_type)}",
        "## Summary",
        _quoted(_clean(summary.summary)),
    ]
    if points:
        sections.extend(("## Key Points", points))
    if tags:
        sections.append(f"Tags: {tags}")
    if summary.user_perspective:
        sections.extend(
            ("## User Perspective", _quoted(_clean(summary.user_perspective)))
        )
    sections.extend(("## Raw Snapshots", snapshot_lines))
    return "\n\n".join(sections) + "\n"


def _render_log_entry(kind: str, raw_path: str, canonical_path: str) -> str:
    return (
        f"- {kind} "
        f"raw=[{raw_path}]({raw_path}) canonical=[{canonical_path}]({canonical_path})"
    )


def _render_index_entry(
    summary: LlmWikiSummary, source_title: str, canonical_path: str
) -> str:
    title = summary.title or source_title
    return f"- [{_inline(title)}]({canonical_path}): {_inline(summary.summary)}"


def _append_once(path: Path, entry: str, marker: str) -> None:
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if any(line.strip() == marker for line in existing.splitlines()):
        return
    prefix = existing.rstrip()
    updated = f"{prefix}\n\n" if prefix else ""
    _atomic_write_text(path, f"{updated}{entry}\n{marker}\n")


def _has_marker(path: Path, marker: str) -> bool:
    if not path.exists():
        return False
    return any(
        line.strip() == marker
        for line in path.read_text(encoding="utf-8").splitlines()
    )


def _text_matches(path: Path, expected: str) -> bool:
    return path.exists() and path.read_text(encoding="utf-8") == expected


def _log_marker(record_id: str) -> str:
    return f"<!-- oracle-llmwiki-log:{record_id} -->"


def _index_marker(record_id: str) -> str:
    return f"<!-- oracle-llmwiki-index:{record_id} -->"


def _inline(value: object) -> str:
    text = html.escape(_clean(value).replace("\n", " "), quote=False)
    return re.sub(r"([\\`*_{}\[\]#|])", r"\\\1", text)


def _quoted(value: object) -> str:
    text = html.escape(_clean(value), quote=False)
    return "\n".join(f"> {line}" if line else ">" for line in text.splitlines()) or ">"


def _clean(value: object) -> str:
    text = sanitize_knowledge_text(str(value)).replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(
        r"<!--\s*oracle-llmwiki-(?:log|index):[^>]*-->",
        "[REDACTED_MARKER]",
        text,
        flags=re.IGNORECASE,
    )
    return "".join(
        character
        for character in text
        if character in {"\n", "\t"} or (ord(character) >= 32 and ord(character) != 127)
    )


def _json_safe(value: object) -> object:
    if isinstance(value, str):
        return _clean(value)
    if isinstance(value, Mapping):
        return {str(_clean(key)): _json_safe(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return [_json_safe(item) for item in value]
    if value is None or isinstance(value, bool | int | float):
        return value
    return _clean(value)


def _write_raw_exclusive(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        return


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
        temporary_path.replace(path)
    except Exception:
        with suppress(OSError):
            os.close(descriptor)
        temporary_path.unlink(missing_ok=True)
        raise


def _runtime_path(workspace_root: Path, relative: str) -> Path:
    return _contained_path(workspace_root, f"runtime/llmwiki/{relative}")


def _contained_path(root: Path, relative: str) -> Path:
    if "\\" in relative:
        raise ValueError("unsafe relative path")
    pure = PurePosixPath(relative)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise ValueError("unsafe relative path")
    candidate = root.joinpath(*pure.parts)
    try:
        root_key = _resolved_path_key(root)
        candidate_key = _resolved_path_key(candidate)
        if os.path.commonpath((root_key, candidate_key)) != root_key:
            raise ValueError("resolved path is outside root")
    except (OSError, ValueError) as exc:
        raise ValueError("path escapes root") from exc
    return candidate


def _is_inside(candidate: Path, root: Path) -> bool:
    try:
        candidate_key = _resolved_path_key(candidate)
        root_key = _resolved_path_key(root)
        return os.path.commonpath((candidate_key, root_key)) == root_key
    except (OSError, ValueError):
        return False


def _vault_lock(vault_root: Path) -> threading.RLock:
    key = _resolved_path_key(vault_root)
    with _LOCKS_GUARD:
        return _VAULT_LOCKS.setdefault(key, threading.RLock())


def _resolved_path_key(path: Path) -> str:
    resolved = str(path.resolve())
    if resolved.startswith("\\\\?\\UNC\\"):
        resolved = "\\\\" + resolved[8:]
    elif resolved.startswith("\\\\?\\"):
        resolved = resolved[4:]
    return os.path.normcase(os.path.normpath(resolved))


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


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
