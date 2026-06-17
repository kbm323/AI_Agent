"""Google Drive artifact writer module (Sub-AC 19a-2).

Writes meeting artifacts — transcripts, context packets, and decisions —
to Google Drive with correct MIME types and folder placement.  Designed
for the OpenClaw tool-use executor to persist meeting outputs to the
L0 knowledge layer.

The module is fully testable with a mock Drive API: every HTTP call to
Google Drive routes through an injectable handler.  Tests inject a mock
that verifies URL, method, headers, and body without real network calls.

Usage::

    from src.gdrive_auth import GDriveAuthenticator, GDriveAuthConfig
    from src.gdrive_artifact_writer import (
        GDriveArtifactWriter, GDriveWriterConfig, ArtifactType,
    )

    auth = GDriveAuthenticator(config)
    token = auth.get_valid_token()
    writer = GDriveArtifactWriter(GDriveWriterConfig())
    result = writer.write_transcript(
        token=token.token,
        meeting_id="meeting_20260610_abc123",
        content="# Meeting Transcript\\n\\nRound 1...",
        round_number=1,
    )

Drive folder structure::

    AI_Agent_Meetings/          ← configurable root name
      meeting_20260610_abc123/
        transcripts/
          round_1_transcript.md
          round_2_transcript.md
        context_packets/
          round_1_role_ceo.json
          round_1_role_cto.json
        decisions/
          decision_001.json
          decision_002.json
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

from .gdrive_auth import GDriveToken


# ═════════════════════════════════════════════════════════════════════════
# Constants
# ═════════════════════════════════════════════════════════════════════════

DEFAULT_ROOT_FOLDER_NAME = "AI_Agent_Meetings"
"""Default name for the top-level Google Drive folder."""

DRIVE_API_BASE = "https://www.googleapis.com/drive/v3"
"""Base URL for Google Drive API v3."""

DRIVE_UPLOAD_BASE = "https://www.googleapis.com/upload/drive/v3"
"""Base URL for Google Drive upload endpoint."""

FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"
"""MIME type for Drive folders."""

MIME_TYPES: dict[str, str] = {
    "transcript": "text/markdown",
    "context_packet": "application/json",
    "decision": "application/json",
    "manifest": "application/json",
}
"""Canonical MIME types per artifact category."""

DEFAULT_REQUEST_TIMEOUT: float = 30.0
"""Default HTTP request timeout in seconds."""


# ═════════════════════════════════════════════════════════════════════════
# Data types
# ═════════════════════════════════════════════════════════════════════════


class ArtifactType(str, Enum):
    """Categories of meeting artifacts that can be written to Drive."""

    TRANSCRIPT = "transcript"
    CONTEXT_PACKET = "context_packet"
    DECISION = "decision"
    MANIFEST = "manifest"


@dataclass(frozen=True)
class GDriveWriterConfig:
    """Configuration for the Google Drive artifact writer.

    Attributes:
        root_folder_name: Top-level folder name in Google Drive.
        request_timeout: HTTP request timeout in seconds.
    """

    root_folder_name: str = DEFAULT_ROOT_FOLDER_NAME
    request_timeout: float = DEFAULT_REQUEST_TIMEOUT

    def __post_init__(self) -> None:
        if not self.root_folder_name.strip():
            raise ValueError("root_folder_name must not be empty")
        if self.request_timeout <= 0:
            raise ValueError("request_timeout must be positive")


@dataclass(frozen=True)
class GDriveFolder:
    """Represents a Google Drive folder.

    Attributes:
        folder_id: Drive file ID for the folder.
        folder_name: Human-readable folder name.
        parent_id: Drive file ID of the parent folder (or empty for root).
    """

    folder_id: str
    folder_name: str
    parent_id: str = ""


@dataclass(frozen=True)
class GDriveFile:
    """Represents a file written to Google Drive.

    Attributes:
        file_id: Drive file ID.
        file_name: File name including extension.
        mime_type: MIME type of the file.
        web_view_link: URL to view the file in browser.
        folder_id: Parent folder ID.
    """

    file_id: str
    file_name: str
    mime_type: str
    web_view_link: str = ""
    folder_id: str = ""


@dataclass(frozen=True)
class GDriveArtifactResult:
    """Result of a Google Drive artifact write operation.

    Attributes:
        success: True when the artifact was written successfully.
        file: The resulting ``GDriveFile`` on success.
        error: Human-readable error description on failure.
        http_status: HTTP status code from the Drive API.
        execution_id: Correlates this result with execution tracking.
    """

    success: bool
    file: GDriveFile | None = None
    error: str = ""
    http_status: int = 0
    execution_id: str = ""


# ═════════════════════════════════════════════════════════════════════════
# Mock-injectable Drive API handler
# ═════════════════════════════════════════════════════════════════════════

#: Injectable function for Google Drive API HTTP calls.
#: Defaults to ``_http_drive_api_call`` which uses urllib.
#: Signature: (method, url, headers, body, timeout) -> (status, response_dict)
_drive_api_fn: Callable[..., tuple[int, dict]] | None = None


def _get_drive_api_fn() -> Callable[..., tuple[int, dict]]:
    """Return the current Drive API handler (real or mock)."""
    global _drive_api_fn
    if _drive_api_fn is not None:
        return _drive_api_fn
    return _http_drive_api_call


def inject_drive_api(handler: Callable[..., tuple[int, dict]] | None) -> None:
    """Replace the Drive API HTTP handler (for testing).

    Args:
        handler: A callable with signature
                 ``(method, url, headers, body, timeout) -> (http_status, response_dict)``,
                 or ``None`` to restore the default HTTP handler.
    """
    global _drive_api_fn
    _drive_api_fn = handler


def _http_drive_api_call(
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes | None = None,
    timeout: float = DEFAULT_REQUEST_TIMEOUT,
) -> tuple[int, dict]:
    """Perform an HTTP call to the Google Drive API.

    Uses only the standard library (urllib) to avoid external
    dependencies.  Returns (http_status, json_response_dict).

    Raises:
        OSError: On network failure.
        json.JSONDecodeError: On non-JSON response.
        ValueError: On malformed URL.
    """
    import urllib.error
    import urllib.request

    data = body if body is not None else b""
    req = urllib.request.Request(url, data=data, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp_body = resp.read().decode("utf-8")
            if not resp_body.strip():
                return (resp.status, {})
            return (resp.status, json.loads(resp_body))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8") if e.fp else ""
        try:
            error_data: dict = json.loads(error_body)
        except json.JSONDecodeError:
            error_data = {"error": {"message": error_body}}
        return (e.code, error_data)
    except urllib.error.URLError as e:
        raise OSError(f"Drive API network error: {e.reason}") from e


# ═════════════════════════════════════════════════════════════════════════
# Folder management
# ═════════════════════════════════════════════════════════════════════════


def _build_auth_header(token: GDriveToken) -> str:
    """Build the Authorization header value from a token."""
    return f"{token.token_type} {token.access_token}"


def _find_folder(
    token: GDriveToken,
    folder_name: str,
    parent_id: str | None = None,
    *,
    timeout: float = DEFAULT_REQUEST_TIMEOUT,
) -> GDriveFolder | None:
    """Search for a folder by name, optionally under a parent.

    Returns:
        ``GDriveFolder`` if found, ``None`` if not found.
    """
    api_fn = _get_drive_api_fn()
    query = (
        f"name='{folder_name}' and mimeType='{FOLDER_MIME_TYPE}'"
        f" and trashed=false"
    )
    if parent_id:
        query += f" and '{parent_id}' in parents"

    url = (
        f"{DRIVE_API_BASE}/files"
        f"?q={_url_encode(query)}"
        f"&fields=files(id,name,parents)"
        f"&pageSize=1"
    )

    headers = {
        "Authorization": _build_auth_header(token),
        "Accept": "application/json",
    }

    status, response = api_fn("GET", url, headers, None, timeout)

    if status >= 400:
        return None

    files = response.get("files", [])
    if not files:
        return None

    f = files[0]
    parents = f.get("parents", [])
    return GDriveFolder(
        folder_id=f["id"],
        folder_name=f["name"],
        parent_id=parents[0] if parents else "",
    )


def find_or_create_folder(
    token: GDriveToken,
    folder_name: str,
    parent_id: str | None = None,
    *,
    timeout: float = DEFAULT_REQUEST_TIMEOUT,
) -> tuple[GDriveFolder, bool]:
    """Find an existing folder or create it if it does not exist.

    Args:
        token: Valid ``GDriveToken``.
        folder_name: Name of the folder to find or create.
        parent_id: Optional parent folder ID. Creates at root if omitted.
        timeout: HTTP request timeout.

    Returns:
        A ``(GDriveFolder, created)`` tuple.  ``created`` is True when
        the folder was just created, False when it already existed.
    """
    existing = _find_folder(token, folder_name, parent_id, timeout=timeout)
    if existing is not None:
        return (existing, False)

    created = _create_folder(token, folder_name, parent_id, timeout=timeout)
    return (created, True)


def _create_folder(
    token: GDriveToken,
    folder_name: str,
    parent_id: str | None = None,
    *,
    timeout: float = DEFAULT_REQUEST_TIMEOUT,
) -> GDriveFolder:
    """Create a new folder in Google Drive.

    Args:
        token: Valid ``GDriveToken``.
        folder_name: Name for the new folder.
        parent_id: Optional parent folder ID.
        timeout: HTTP request timeout.

    Returns:
        The newly created ``GDriveFolder``.

    Raises:
        OSError: On network failure.
        ValueError: On API error (non-2xx, non-409).
    """
    api_fn = _get_drive_api_fn()

    metadata: dict[str, Any] = {
        "name": folder_name,
        "mimeType": FOLDER_MIME_TYPE,
    }
    if parent_id:
        metadata["parents"] = [parent_id]

    body = json.dumps(metadata).encode("utf-8")
    headers = {
        "Authorization": _build_auth_header(token),
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    url = f"{DRIVE_API_BASE}/files?fields=id,name,parents"
    status, response = api_fn("POST", url, headers, body, timeout)

    if status >= 400:
        error_msg = response.get("error", {}).get("message", f"HTTP {status}")
        raise ValueError(f"Failed to create folder '{folder_name}': {error_msg}")

    parents = response.get("parents", [])
    return GDriveFolder(
        folder_id=response["id"],
        folder_name=response.get("name", folder_name),
        parent_id=parents[0] if parents else (parent_id or ""),
    )


# ═════════════════════════════════════════════════════════════════════════
# Artifact writing
# ═════════════════════════════════════════════════════════════════════════


def write_artifact(
    token: GDriveToken,
    folder_id: str,
    file_name: str,
    content: str | bytes,
    mime_type: str,
    *,
    timeout: float = DEFAULT_REQUEST_TIMEOUT,
) -> GDriveArtifactResult:
    """Write a single artifact to a Google Drive folder.

    Uses multipart upload: metadata (JSON) + content in a single request.

    Args:
        token: Valid ``GDriveToken``.
        folder_id: Parent folder Drive ID.
        file_name: Name of the file to create.
        content: File content as string or bytes.
        mime_type: MIME type for the file.
        timeout: HTTP request timeout.

    Returns:
        ``GDriveArtifactResult`` indicating success or failure.
    """
    if not folder_id:
        return GDriveArtifactResult(
            success=False,
            error="folder_id is required",
            http_status=0,
        )

    if not file_name:
        return GDriveArtifactResult(
            success=False,
            error="file_name is required",
            http_status=0,
        )

    content_bytes = content.encode("utf-8") if isinstance(content, str) else content

    api_fn = _get_drive_api_fn()

    # Build multipart body
    metadata = json.dumps({
        "name": file_name,
        "parents": [folder_id],
        "mimeType": mime_type,
    })

    boundary = f"hermes_artifact_{int(time.time() * 1_000_000)}"
    body_parts = [
        f"--{boundary}",
        "Content-Type: application/json; charset=UTF-8",
        "",
        metadata,
        f"--{boundary}",
        f"Content-Type: {mime_type}",
        "",
        content_bytes.decode("utf-8") if isinstance(content_bytes, bytes) else str(content_bytes),
        f"--{boundary}--",
    ]
    body = "\r\n".join(body_parts).encode("utf-8")

    headers = {
        "Authorization": _build_auth_header(token),
        "Content-Type": f"multipart/related; boundary={boundary}",
        "Accept": "application/json",
    }

    url = f"{DRIVE_UPLOAD_BASE}/files?uploadType=multipart&fields=id,name,mimeType,webViewLink,parents"

    try:
        status, response = api_fn("POST", url, headers, body, timeout)
    except OSError as exc:
        return GDriveArtifactResult(
            success=False,
            error=f"Network error writing '{file_name}': {exc}",
            http_status=0,
        )

    if status >= 400:
        error_msg = response.get("error", {}).get("message", f"HTTP {status}")
        return GDriveArtifactResult(
            success=False,
            error=f"Failed to write '{file_name}': {error_msg}",
            http_status=status,
        )

    parents = response.get("parents", [])
    return GDriveArtifactResult(
        success=True,
        file=GDriveFile(
            file_id=response.get("id", ""),
            file_name=response.get("name", file_name),
            mime_type=response.get("mimeType", mime_type),
            web_view_link=response.get("webViewLink", ""),
            folder_id=parents[0] if parents else folder_id,
        ),
    )


def write_text_artifact(
    token: GDriveToken,
    folder_id: str,
    file_name: str,
    content: str,
    mime_type: str = "text/plain",
    *,
    timeout: float = DEFAULT_REQUEST_TIMEOUT,
) -> GDriveArtifactResult:
    """Write a plain-text artifact to a Google Drive folder.

    Uses simple multipart upload.  Convenience wrapper around
    ``write_artifact`` for text content.

    Args:
        token: Valid ``GDriveToken``.
        folder_id: Parent folder Drive ID.
        file_name: Name of the file to create.
        content: Text content.
        mime_type: MIME type (default: text/plain).
        timeout: HTTP request timeout.

    Returns:
        ``GDriveArtifactResult``.
    """
    return write_artifact(
        token=token,
        folder_id=folder_id,
        file_name=file_name,
        content=content,
        mime_type=mime_type,
        timeout=timeout,
    )


def write_json_artifact(
    token: GDriveToken,
    folder_id: str,
    file_name: str,
    content: dict | list,
    *,
    timeout: float = DEFAULT_REQUEST_TIMEOUT,
) -> GDriveArtifactResult:
    """Write a JSON artifact to a Google Drive folder.

    The content dict/list is serialized with ``json.dumps`` before upload.

    Args:
        token: Valid ``GDriveToken``.
        folder_id: Parent folder Drive ID.
        file_name: Name of the file (should end with .json).
        content: JSON-serializable dict or list.
        timeout: HTTP request timeout.

    Returns:
        ``GDriveArtifactResult``.
    """
    json_str = json.dumps(content, ensure_ascii=False, indent=2)
    return write_artifact(
        token=token,
        folder_id=folder_id,
        file_name=file_name,
        content=json_str,
        mime_type="application/json",
        timeout=timeout,
    )


# ═════════════════════════════════════════════════════════════════════════
# URL encoding helper
# ═════════════════════════════════════════════════════════════════════════


def _url_encode(s: str) -> str:
    """Percent-encode a string for use in a Drive API query parameter.

    Uses urllib.parse.quote with safe=''.
    """
    import urllib.parse

    return urllib.parse.quote(s, safe="")


# ═════════════════════════════════════════════════════════════════════════
# GDriveArtifactWriter — high-level API
# ═════════════════════════════════════════════════════════════════════════


class GDriveArtifactWriter:
    """High-level writer for meeting artifacts to Google Drive.

    Manages the folder hierarchy and writes transcripts, context packets,
    and decisions with correct MIME types.

    Usage::

        config = GDriveWriterConfig(root_folder_name="AI_Agent_Meetings")
        writer = GDriveArtifactWriter(config)

        token = auth.get_valid_token().token

        result = writer.write_transcript(
            token=token,
            meeting_id="meeting_20260610_abc123",
            content="# Round 1 Transcript\\n\\n...",
            round_number=1,
        )
    """

    def __init__(self, config: GDriveWriterConfig | None = None) -> None:
        self._config = config or GDriveWriterConfig()
        self._folder_cache: dict[str, GDriveFolder] = {}

    @property
    def config(self) -> GDriveWriterConfig:
        return self._config

    # ── Folder hierarchy ──────────────────────────────────────────────

    def ensure_meeting_folder(
        self,
        token: GDriveToken,
        meeting_id: str,
    ) -> tuple[GDriveFolder, bool]:
        """Ensure the full folder hierarchy for a meeting exists.

        Creates::

            {root_folder_name}/
              {meeting_id}/
                transcripts/
                context_packets/
                decisions/

        Returns:
            ``(meeting_folder, created)`` — the meeting-level folder and
            whether the hierarchy was newly created.

        Raises:
            ValueError: On Drive API error.
            OSError: On network failure.
        """
        timeout = self._config.request_timeout

        # 1) Root folder
        root_folder, _ = find_or_create_folder(
            token, self._config.root_folder_name, timeout=timeout
        )

        # 2) Meeting folder
        meeting_folder, _ = find_or_create_folder(
            token, meeting_id, parent_id=root_folder.folder_id, timeout=timeout
        )

        # 3) Sub-folders for each artifact type
        sub_names = ["transcripts", "context_packets", "decisions"]
        all_existed = True
        for sub_name in sub_names:
            _, created = find_or_create_folder(
                token,
                sub_name,
                parent_id=meeting_folder.folder_id,
                timeout=timeout,
            )
            if created:
                all_existed = False

        # Cache the meeting folder
        cache_key = f"{meeting_id}"
        self._folder_cache[cache_key] = meeting_folder

        return (meeting_folder, not all_existed)

    def _get_sub_folder(
        self,
        token: GDriveToken,
        meeting_id: str,
        sub_name: str,
    ) -> GDriveFolder:
        """Get (or create) a sub-folder for a specific artifact type."""
        timeout = self._config.request_timeout

        # Cache key includes meeting_id + sub_name
        cache_key = f"{meeting_id}/{sub_name}"
        if cache_key in self._folder_cache:
            return self._folder_cache[cache_key]

        # Ensure meeting folder hierarchy exists
        meeting_folder, _ = self.ensure_meeting_folder(token, meeting_id)

        sub_folder, _ = find_or_create_folder(
            token,
            sub_name,
            parent_id=meeting_folder.folder_id,
            timeout=timeout,
        )
        self._folder_cache[cache_key] = sub_folder
        return sub_folder

    # ── Transcripts ───────────────────────────────────────────────────

    def write_transcript(
        self,
        token: GDriveToken,
        meeting_id: str,
        content: str,
        *,
        round_number: int = 1,
        role_id: str = "",
        timeout: float | None = None,
    ) -> GDriveArtifactResult:
        """Write a meeting transcript to Drive.

        Args:
            token: Valid ``GDriveToken``.
            meeting_id: Meeting identifier.
            content: Transcript text (markdown format recommended).
            round_number: Meeting round number (1–4).
            role_id: Optional role identifier for per-role transcripts.
            timeout: Optional per-call timeout override.

        Returns:
            ``GDriveArtifactResult``.
        """
        effective_timeout = timeout or self._config.request_timeout
        sub_folder = self._get_sub_folder(
            token, meeting_id, "transcripts"
        )

        if role_id:
            file_name = f"round_{round_number}_{role_id}_transcript.md"
        else:
            file_name = f"round_{round_number}_transcript.md"

        return write_artifact(
            token=token,
            folder_id=sub_folder.folder_id,
            file_name=file_name,
            content=content,
            mime_type=MIME_TYPES["transcript"],
            timeout=effective_timeout,
        )

    # ── Context packets ───────────────────────────────────────────────

    def write_context_packet(
        self,
        token: GDriveToken,
        meeting_id: str,
        content: dict,
        *,
        round_number: int = 1,
        role_id: str = "",
        timeout: float | None = None,
    ) -> GDriveArtifactResult:
        """Write a context packet (JSON) to Drive.

        Args:
            token: Valid ``GDriveToken``.
            meeting_id: Meeting identifier.
            content: Context packet as a JSON-serializable dict.
            round_number: Meeting round number (1–4).
            role_id: Optional role identifier for per-role packets.
            timeout: Optional per-call timeout override.

        Returns:
            ``GDriveArtifactResult``.
        """
        effective_timeout = timeout or self._config.request_timeout
        sub_folder = self._get_sub_folder(
            token, meeting_id, "context_packets"
        )

        if role_id:
            file_name = f"round_{round_number}_role_{role_id}_packet.json"
        else:
            file_name = f"round_{round_number}_packet.json"

        return write_json_artifact(
            token=token,
            folder_id=sub_folder.folder_id,
            file_name=file_name,
            content=content,
            timeout=effective_timeout,
        )

    # ── Decisions ─────────────────────────────────────────────────────

    def write_decision(
        self,
        token: GDriveToken,
        meeting_id: str,
        content: dict,
        *,
        decision_index: int = 1,
        timeout: float | None = None,
    ) -> GDriveArtifactResult:
        """Write a meeting decision record (JSON) to Drive.

        Args:
            token: Valid ``GDriveToken``.
            meeting_id: Meeting identifier.
            content: Decision record as a JSON-serializable dict.
            decision_index: Sequential decision number.
            timeout: Optional per-call timeout override.

        Returns:
            ``GDriveArtifactResult``.
        """
        effective_timeout = timeout or self._config.request_timeout
        sub_folder = self._get_sub_folder(
            token, meeting_id, "decisions"
        )

        file_name = f"decision_{decision_index:03d}.json"

        return write_json_artifact(
            token=token,
            folder_id=sub_folder.folder_id,
            file_name=file_name,
            content=content,
            timeout=effective_timeout,
        )

    # ── Manifest ──────────────────────────────────────────────────────

    def write_manifest(
        self,
        token: GDriveToken,
        meeting_id: str,
        content: dict,
        *,
        timeout: float | None = None,
    ) -> GDriveArtifactResult:
        """Write the meeting manifest to the meeting folder root.

        Args:
            token: Valid ``GDriveToken``.
            meeting_id: Meeting identifier.
            content: Manifest dict (JSON-serializable).
            timeout: Optional per-call timeout override.

        Returns:
            ``GDriveArtifactResult``.
        """
        effective_timeout = timeout or self._config.request_timeout
        meeting_folder, _ = self.ensure_meeting_folder(token, meeting_id)

        return write_json_artifact(
            token=token,
            folder_id=meeting_folder.folder_id,
            file_name="manifest.json",
            content=content,
            timeout=effective_timeout,
        )

    # ── Batch write ───────────────────────────────────────────────────

    def write_artifacts_batch(
        self,
        token: GDriveToken,
        meeting_id: str,
        *,
        transcripts: list[dict] | None = None,
        context_packets: list[dict] | None = None,
        decisions: list[dict] | None = None,
        manifest: dict | None = None,
        timeout: float | None = None,
    ) -> list[GDriveArtifactResult]:
        """Write multiple artifacts in a batch.

        Each input list item is a dict with keys matching the individual
        write method parameters.

        Args:
            token: Valid ``GDriveToken``.
            meeting_id: Meeting identifier.
            transcripts: List of transcript specs
                ``{content, round_number?, role_id?}``.
            context_packets: List of context packet specs
                ``{content, round_number?, role_id?}``.
            decisions: List of decision specs
                ``{content, decision_index?}``.
            manifest: Optional manifest dict.
            timeout: Optional per-call timeout override.

        Returns:
            List of ``GDriveArtifactResult``, one per artifact written,
            in order: transcripts, context_packets, decisions, manifest.
        """
        results: list[GDriveArtifactResult] = []
        effective_timeout = timeout or self._config.request_timeout

        # Ensure folder hierarchy once
        self.ensure_meeting_folder(token, meeting_id)

        for t in (transcripts or []):
            results.append(
                self.write_transcript(
                    token=token,
                    meeting_id=meeting_id,
                    content=t["content"],
                    round_number=t.get("round_number", 1),
                    role_id=t.get("role_id", ""),
                    timeout=effective_timeout,
                )
            )

        for cp in (context_packets or []):
            results.append(
                self.write_context_packet(
                    token=token,
                    meeting_id=meeting_id,
                    content=cp["content"],
                    round_number=cp.get("round_number", 1),
                    role_id=cp.get("role_id", ""),
                    timeout=effective_timeout,
                )
            )

        for d in (decisions or []):
            results.append(
                self.write_decision(
                    token=token,
                    meeting_id=meeting_id,
                    content=d["content"],
                    decision_index=d.get("decision_index", len(results) + 1),
                    timeout=effective_timeout,
                )
            )

        if manifest is not None:
            results.append(
                self.write_manifest(
                    token=token,
                    meeting_id=meeting_id,
                    content=manifest,
                    timeout=effective_timeout,
                )
            )

        return results

    # ── Cache management ──────────────────────────────────────────────

    def clear_folder_cache(self) -> None:
        """Clear the internal folder ID cache.

        Useful between test runs or when Drive state may have changed
        externally.
        """
        self._folder_cache.clear()


# ═════════════════════════════════════════════════════════════════════════
# Module-level convenience
# ═════════════════════════════════════════════════════════════════════════


def build_writer(
    root_folder_name: str = DEFAULT_ROOT_FOLDER_NAME,
    request_timeout: float = DEFAULT_REQUEST_TIMEOUT,
) -> GDriveArtifactWriter:
    """Build a ``GDriveArtifactWriter`` from individual parameters.

    Args:
        root_folder_name: Top-level folder name in Google Drive.
        request_timeout: HTTP request timeout in seconds.

    Returns:
        A configured ``GDriveArtifactWriter`` instance.
    """
    config = GDriveWriterConfig(
        root_folder_name=root_folder_name,
        request_timeout=request_timeout,
    )
    return GDriveArtifactWriter(config)
