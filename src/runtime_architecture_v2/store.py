"""File-backed MeetingRun store for Runtime Architecture v2.

The store owns only AI_Agent project-local artifacts. Hermes-native state remains
referenced by ID inside schema ``hermes_refs`` fields.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .schemas import MeetingOutcome, MeetingRun, MeetingRunState, RecoveryCheckpoint

if TYPE_CHECKING:
    from .multi_bot import MultiBotSession

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")
_RUNTIME_LAYOUT_DIRS = (
    "packets",
    "worker_outputs",
    "validation",
    "discord_projection",
    "checkpoints",
)


@dataclass
class StoreError(Exception):
    """Structured storage error suitable for recovery/audit surfaces."""

    code: str
    message: str
    meeting_run_id: str = ""
    path: str = ""

    def __str__(self) -> str:
        parts = [self.code, self.message]
        if self.meeting_run_id:
            parts.append(f"meeting_run_id={self.meeting_run_id}")
        if self.path:
            parts.append(f"path={self.path}")
        return " | ".join(parts)


class MeetingRunStore:
    """Persist MeetingRun state under ``runtime/meeting_runs/<id>/``."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.runtime_root = self.root / "runtime" / "meeting_runs"

    def meeting_run_dir(self, meeting_run_id: str) -> Path:
        self._validate_id(meeting_run_id, "meeting_run_id")
        return self.runtime_root / meeting_run_id

    def save_meeting_run(self, meeting_run: MeetingRun) -> Path:
        run_dir = self._ensure_run_layout(meeting_run.meeting_run_id)
        path = run_dir / "meeting_run.json"
        self._atomic_write_json(path, meeting_run.to_dict())
        return path

    def load_meeting_run(self, meeting_run_id: str) -> MeetingRun:
        path = self.meeting_run_dir(meeting_run_id) / "meeting_run.json"
        if not path.exists():
            raise StoreError(
                code="missing_meeting_run",
                message="meeting_run.json does not exist",
                meeting_run_id=meeting_run_id,
                path=str(path),
            )
        try:
            payload = self._read_json(path)
            return MeetingRun.from_dict(payload)
        except Exception as exc:
            raise StoreError(
                code="corrupt_meeting_run",
                message="corrupt data: meeting run reconstruction failed",
                meeting_run_id=meeting_run_id,
                path=str(path),
            ) from exc

    def save_meeting_session(
        self,
        session: MultiBotSession,
        *,
        meeting_run_id: str | None = None,
    ) -> Path:
        resolved_id = meeting_run_id or session.meeting_run_id
        self._require_matching_run_id(resolved_id, session.meeting_run_id)
        path = self._ensure_run_layout(resolved_id) / "meeting_session.json"
        self._atomic_write_json(path, session.to_dict())
        return path

    def load_meeting_session(self, meeting_run_id: str) -> MultiBotSession:
        from .multi_bot import MultiBotSession

        path = self.meeting_run_dir(meeting_run_id) / "meeting_session.json"
        if not path.exists():
            raise StoreError(
                code="missing_meeting_session",
                message="meeting_session.json does not exist",
                meeting_run_id=meeting_run_id,
                path=str(path),
            )
        try:
            session = MultiBotSession.from_dict(self._read_json(path))
            self._require_matching_run_id(meeting_run_id, session.meeting_run_id)
            return session
        except Exception as exc:
            raise StoreError(
                code="corrupt_meeting_session",
                message="corrupt data: meeting session reconstruction failed",
                meeting_run_id=meeting_run_id,
                path=str(path),
            ) from exc

    def save_meeting_outcome(
        self,
        outcome: MeetingOutcome,
        *,
        meeting_run_id: str | None = None,
    ) -> Path:
        resolved_id = meeting_run_id or outcome.meeting_run_id
        self._require_matching_run_id(resolved_id, outcome.meeting_run_id)
        path = self._ensure_run_layout(resolved_id) / "meeting_outcome.json"
        self._atomic_write_json(path, outcome.to_dict())
        return path

    def load_meeting_outcome(self, meeting_run_id: str) -> MeetingOutcome:
        path = self.meeting_run_dir(meeting_run_id) / "meeting_outcome.json"
        if not path.exists():
            raise StoreError(
                code="missing_meeting_outcome",
                message="meeting_outcome.json does not exist",
                meeting_run_id=meeting_run_id,
                path=str(path),
            )
        try:
            outcome = MeetingOutcome.from_dict(self._read_json(path))
            self._require_matching_run_id(meeting_run_id, outcome.meeting_run_id)
            return outcome
        except Exception as exc:
            raise StoreError(
                code="corrupt_meeting_outcome",
                message="corrupt data: meeting outcome reconstruction failed",
                meeting_run_id=meeting_run_id,
                path=str(path),
            ) from exc

    def find_by_discord_thread_id(self, thread_id: str) -> MeetingRun | None:
        self._validate_id(thread_id, "discord_thread_id")
        if not self.runtime_root.exists():
            return None

        matches: list[MeetingRun] = []
        for path in sorted(self.runtime_root.glob("*/meeting_run.json")):
            try:
                run = MeetingRun.from_dict(self._read_json(path))
                discord = dict(run.trigger.get("discord") or {})
                linked = str(
                    run.metadata.get("discord_thread_id")
                    or discord.get("thread_id")
                    or ""
                )
            except (StoreError, KeyError, TypeError, ValueError):
                continue
            if linked == thread_id:
                matches.append(run)

        if not matches:
            return None
        return sorted(matches, key=lambda run: run.meeting_run_id)[-1]

    def reserve_gateway_invocation(
        self,
        invocation_key: str,
        *,
        created_at_epoch: float,
        expires_after_seconds: float | None,
    ) -> tuple[bool, dict[str, Any]]:
        """Atomically reserve one Gateway invocation across processes."""

        self._validate_id(invocation_key, "invocation_key")
        directory = self.root / "runtime" / "gateway_invocations"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{invocation_key}.json"
        initial = {
            "invocation_key": invocation_key,
            "created_at_epoch": created_at_epoch,
            "completed": False,
        }
        while True:
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
            except FileExistsError:
                existing = self._read_json(path)
                if expires_after_seconds is None:
                    return False, existing
                age = created_at_epoch - float(existing.get("created_at_epoch", 0.0))
                if age <= expires_after_seconds:
                    return False, existing
                try:
                    path.unlink()
                except FileNotFoundError:
                    continue
            else:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(initial, handle, ensure_ascii=False, sort_keys=True)
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                return True, initial

    def complete_gateway_invocation(
        self,
        invocation_key: str,
        payload: dict[str, Any],
    ) -> Path:
        """Persist the reusable result for a reserved Gateway invocation."""

        self._validate_id(invocation_key, "invocation_key")
        path = (
            self.root
            / "runtime"
            / "gateway_invocations"
            / f"{invocation_key}.json"
        )
        existing = self._read_json(path) if path.exists() else {}
        self._atomic_write_json(
            path,
            {
                **existing,
                "invocation_key": invocation_key,
                **payload,
                "completed": True,
            },
        )
        return path

    def save_checkpoint(self, checkpoint: RecoveryCheckpoint) -> Path:
        self._validate_id(checkpoint.checkpoint_id, "checkpoint_id")
        run_dir = self._ensure_run_layout(checkpoint.meeting_run_id)
        path = run_dir / "checkpoints" / f"{checkpoint.checkpoint_id}.json"
        payload = checkpoint.to_dict()
        payload["checkpoint_path"] = str(path)
        self._atomic_write_json(path, payload)
        return path

    def load_checkpoint(
        self,
        meeting_run_id: str,
        checkpoint_id: str,
    ) -> RecoveryCheckpoint:
        self._validate_id(checkpoint_id, "checkpoint_id")
        path = (
            self.meeting_run_dir(meeting_run_id)
            / "checkpoints"
            / f"{checkpoint_id}.json"
        )
        if not path.exists():
            raise StoreError(
                code="missing_checkpoint",
                message="checkpoint json does not exist",
                meeting_run_id=meeting_run_id,
                path=str(path),
            )
        try:
            return RecoveryCheckpoint.from_dict(self._read_json(path))
        except Exception as exc:
            raise StoreError(
                code="corrupt_checkpoint",
                message="corrupt data: checkpoint reconstruction failed",
                meeting_run_id=meeting_run_id,
                path=str(path),
            ) from exc

    def load_latest_checkpoint(self, meeting_run_id: str) -> RecoveryCheckpoint:
        checkpoint_dir = self.meeting_run_dir(meeting_run_id) / "checkpoints"
        if not checkpoint_dir.exists():
            return self._default_checkpoint(meeting_run_id)
        checkpoint_paths = sorted(
            checkpoint_dir.glob("*.json"),
            key=lambda path: (path.stat().st_mtime_ns, path.name),
        )
        if not checkpoint_paths:
            return self._default_checkpoint(meeting_run_id)
        latest_path = checkpoint_paths[-1]
        try:
            return RecoveryCheckpoint.from_dict(self._read_json(latest_path))
        except StoreError:
            raise
        except Exception as exc:
            raise StoreError(
                code="corrupt_checkpoint",
                message=str(exc),
                meeting_run_id=meeting_run_id,
                path=str(latest_path),
            ) from exc

    def append_decision_event(self, meeting_run_id: str, event: dict[str, Any]) -> Path:
        return self._append_jsonl_event(meeting_run_id, "decision_log.jsonl", event)

    def append_audit_event(self, meeting_run_id: str, event: dict[str, Any]) -> Path:
        return self._append_jsonl_event(meeting_run_id, "audit_log.jsonl", event)

    def _append_jsonl_event(
        self, meeting_run_id: str, filename: str, event: dict[str, Any]
    ) -> Path:
        run_dir = self._ensure_run_layout(meeting_run_id)
        path = run_dir / filename
        payload = {
            **event,
            "meeting_run_id": meeting_run_id,
            "logged_at": self._now_iso(),
        }
        line = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        return path

    def _ensure_run_layout(self, meeting_run_id: str) -> Path:
        run_dir = self.meeting_run_dir(meeting_run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        for directory in _RUNTIME_LAYOUT_DIRS:
            (run_dir / directory).mkdir(exist_ok=True)
        return run_dir

    def _atomic_write_json(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent), text=True
        )
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            tmp_path.replace(path)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise

    def _read_json(self, path: Path) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise StoreError(
                code="invalid_json",
                message=str(exc),
                path=str(path),
            ) from exc
        if not isinstance(payload, dict):
            raise StoreError(
                code="invalid_json",
                message="JSON root must be an object",
                path=str(path),
            )
        return payload

    def _default_checkpoint(self, meeting_run_id: str) -> RecoveryCheckpoint:
        return RecoveryCheckpoint(
            checkpoint_id="",
            meeting_run_id=meeting_run_id,
            state=MeetingRunState.CREATED,
            note="no checkpoint found",
        )

    def _require_matching_run_id(self, expected: str, actual: str) -> None:
        self._validate_id(expected, "meeting_run_id")
        self._validate_id(actual, "meeting_run_id")
        if expected != actual:
            raise StoreError(
                code="meeting_run_id_mismatch",
                message=f"meeting_run_id mismatch: expected {expected}, got {actual}",
                meeting_run_id=expected,
            )

    def _validate_id(self, value: str, label: str) -> None:
        if (
            not value
            or value in {".", ".."}
            or value.startswith(".")
            or not _SAFE_ID_RE.fullmatch(value)
        ):
            raise StoreError(
                code=f"invalid {label}",
                message=f"invalid {label}: {value}",
            )

    def _now_iso(self) -> str:
        return datetime.now(UTC).isoformat()


__all__ = ["MeetingRunStore", "StoreError"]
