"""Durable, serialized QMD index maintenance."""

from __future__ import annotations

import errno
import json
import os
import tempfile
import threading
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .qmd_search import QmdClient, QmdCommandResult

_DEBOUNCE_SECONDS = 5.0
_INTERPROCESS_LOCK_TIMEOUT_SECONDS = 30.0
_INTERPROCESS_LOCK_POLL_SECONDS = 0.02
_LOCKS_GUARD = threading.Lock()
_RUNTIME_LOCKS: dict[str, threading.RLock] = {}


@dataclass(frozen=True)
class QmdReconcileResult:
    ok: bool
    updated: bool = False
    embedded: bool = False
    error: str = ""


class _InterProcessFileLock:
    """A bounded kernel lock whose file never indicates ownership."""

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


class QmdIndexScheduler:
    """Coordinate durable QMD refreshes for one AI_Agent workspace."""

    def __init__(self, *, runtime_root: Path, client: QmdClient) -> None:
        self.runtime_root = Path(runtime_root) / "runtime" / "qmd"
        self.client = client
        self._local_lock = _runtime_lock(self.runtime_root)
        self._worker_guard = threading.Lock()
        self._worker: threading.Thread | None = None

    @property
    def dirty(self) -> bool:
        return self._dirty_path.exists()

    @property
    def _dirty_path(self) -> Path:
        return self.runtime_root / "dirty.json"

    @property
    def _lock_path(self) -> Path:
        return self.runtime_root / "index.lock"

    def mark_dirty(self) -> None:
        with self._local_lock, _InterProcessFileLock(self._lock_path):
            _atomic_write_json(self._dirty_path, {"dirty": True})

    def schedule(self) -> bool:
        """Start one debounced daemon worker, coalescing repeat requests."""

        with self._worker_guard:
            if self._worker is not None and self._worker.is_alive():
                return False
            worker = threading.Thread(
                target=self._run_scheduled_reconcile,
                name="qmd-index-reconcile",
                daemon=True,
            )
            self._worker = worker
            worker.start()
            return True

    def refresh_for_search(self) -> QmdCommandResult:
        """Run a serialized incremental update without claiming embeddings are fresh."""

        try:
            with self._local_lock, _InterProcessFileLock(self._lock_path):
                return self.client.update()
        except (OSError, TimeoutError):
            return QmdCommandResult(ok=False, error="command_failed")

    def reconcile(self) -> QmdReconcileResult:
        """Update and embed, clearing the durable marker only after full success."""

        try:
            with self._local_lock, _InterProcessFileLock(self._lock_path):
                update = self.client.update()
                if not update.ok:
                    return QmdReconcileResult(ok=False, error=update.error)
                embed = self.client.embed()
                if not embed.ok:
                    return QmdReconcileResult(
                        ok=False, updated=True, error=embed.error
                    )
                self._dirty_path.unlink(missing_ok=True)
                return QmdReconcileResult(ok=True, updated=True, embedded=True)
        except (OSError, TimeoutError):
            return QmdReconcileResult(ok=False, error="command_failed")

    def _run_scheduled_reconcile(self) -> None:
        try:
            time.sleep(_DEBOUNCE_SECONDS)
            self.reconcile()
        finally:
            with self._worker_guard:
                if self._worker is threading.current_thread():
                    self._worker = None


def _runtime_lock(runtime_root: Path) -> threading.RLock:
    key = _resolved_path_key(runtime_root)
    with _LOCKS_GUARD:
        return _RUNTIME_LOCKS.setdefault(key, threading.RLock())


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


def _atomic_write_json(path: Path, payload: dict[str, bool]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    except Exception:
        with suppress(OSError):
            os.close(descriptor)
        temporary_path.unlink(missing_ok=True)
        raise
