"""Artifact reader module (Sub-AC 19a-3).

Reads and retrieves meeting artifacts from Google Drive by artifact type
and meeting ID.  Mirrors the ``gdrive_artifact_writer`` module's Drive
interaction pattern, using the same mock-injectable HTTP handler so the
reader is fully testable without real network calls.

Usage::

    from src.gdrive_auth import GDriveAuthenticator, GDriveAuthConfig
    from src.gdrive_artifact_reader import (
        ArtifactReader,
        ReaderConfig,
        ArtifactType,
    )

    auth = GDriveAuthenticator(config)
    token = auth.get_valid_token()
    reader = ArtifactReader(ReaderConfig())
    results = reader.query_artifacts(
        token=token.token,
        meeting_id="meeting_20260610_abc123",
        artifact_type="transcript",
    )
    for info in results:
        result = reader.read_artifact_content(token=token.token, file_id=info.file_id)
        print(result.content)

Drive folder structure (same as writer)::

    {root_folder_name}/
      {meeting_id}/
        transcripts/
          round_1_transcript.md
          round_1_ceo_transcript.md
        context_packets/
          round_1_role_ceo_packet.json
        decisions/
          decision_001.json
        manifest.json
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from .gdrive_artifact_writer import (
    DEFAULT_REQUEST_TIMEOUT,
    DEFAULT_ROOT_FOLDER_NAME,
    DRIVE_API_BASE,
    MIME_TYPES,
    _build_auth_header,
    _find_folder,
    _get_drive_api_fn,
    _url_encode,
)
from .gdrive_auth import GDriveToken

# ═════════════════════════════════════════════════════════════════════════
# Data types
# ═════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class ReaderConfig:
    """Configuration for the artifact reader.

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
class ArtifactInfo:
    """Metadata for a single meeting artifact stored in Google Drive.

    Attributes:
        file_id: Drive file ID.
        file_name: File name including extension.
        mime_type: MIME type of the file.
        meeting_id: Meeting identifier extracted from folder path.
        artifact_type: Category (transcript, context_packet, decision, manifest).
        web_view_link: URL to view the file in browser.
        folder_id: Parent folder Drive ID.
        round_number: Meeting round number if parseable from filename, else 0.
        role_id: Role identifier if parseable from filename, else empty.
        decision_index: Decision sequence number if parseable, else 0.
    """

    file_id: str
    file_name: str
    mime_type: str
    meeting_id: str = ""
    artifact_type: str = ""
    web_view_link: str = ""
    folder_id: str = ""
    round_number: int = 0
    role_id: str = ""
    decision_index: int = 0


@dataclass(frozen=True)
class ArtifactReadResult:
    """Result of an artifact read (download) operation.

    Attributes:
        success: True when the artifact was read successfully.
        info: ``ArtifactInfo`` with metadata for the downloaded file.
        content: Raw content string (or None on failure).
        error: Human-readable error description on failure.
        http_status: HTTP status code from the Drive API.
        execution_id: Correlates this result with execution tracking.
    """

    success: bool
    info: ArtifactInfo | None = None
    content: str | None = None
    error: str = ""
    http_status: int = 0
    execution_id: str = ""


@dataclass(frozen=True)
class ArtifactQuery:
    """Query parameters for finding artifacts.

    Attributes:
        meeting_id: Required meeting identifier.
        artifact_type: One of transcript, context_packet, decision, manifest.
        round_number: Optional round filter (1–4).
        role_id: Optional role filter.
        decision_index: Optional decision sequence filter.
    """

    meeting_id: str
    artifact_type: str | None = None
    round_number: int | None = None
    role_id: str | None = None
    decision_index: int | None = None

    def __post_init__(self) -> None:
        if not self.meeting_id.strip():
            raise ValueError("meeting_id must not be empty")
        if self.artifact_type is not None and self.artifact_type not in MIME_TYPES:
            raise ValueError(
                f"artifact_type must be one of {list(MIME_TYPES)}, "
                f"got '{self.artifact_type}'"
            )


# ═════════════════════════════════════════════════════════════════════════
# Filename parsing
# ═════════════════════════════════════════════════════════════════════════


def _parse_transcript_filename(file_name: str) -> tuple[int, str]:
    """Extract round_number and role_id from a transcript filename.

    Examples:
        round_1_transcript.md       -> (1, "")
        round_2_ceo_transcript.md   -> (2, "ceo")
        round_3_role_cto_packet.json -> (3, "cto")
    """
    round_num = 0
    role_id = ""

    name = file_name.removesuffix(".md").removesuffix(".json")
    parts = name.split("_")
    try:
        round_idx = parts.index("round")
        if round_idx + 1 < len(parts):
            round_num = int(parts[round_idx + 1])
    except (ValueError, IndexError):
        pass

    # Between round_N and _transcript or _packet is the role.
    # Strip the optional "role" prefix (e.g. "role_cto" -> "cto").
    t_idx = -1
    for marker in ("transcript", "packet"):
        try:
            t_idx = parts.index(marker)
            break
        except ValueError:
            continue

    if t_idx > 1:
        # parts: ["round", "1", "role", "cto", "packet"]
        middle = parts[2:t_idx]
        # Remove leading "role" element if present
        if middle and middle[0] == "role":
            middle = middle[1:]
        if middle:
            role_id = "_".join(middle)

    return (round_num, role_id)


def _parse_decision_filename(file_name: str) -> int:
    """Extract decision_index from a decision filename.

    Examples:
        decision_001.json  -> 1
        decision_042.json  -> 42
    """
    name = file_name.removesuffix(".json")
    parts = name.split("_")
    try:
        return int(parts[-1])
    except (ValueError, IndexError):
        return 0


def _infer_artifact_type(file_name: str, parent_folder_name: str = "") -> str:
    """Infer the artifact type from filename and/or parent folder name.

    Priority: parent folder name > filename heuristics.
    """
    # Parent folder is authoritative
    folder_to_type = {
        "transcripts": "transcript",
        "context_packets": "context_packet",
        "decisions": "decision",
    }
    if parent_folder_name in folder_to_type:
        return folder_to_type[parent_folder_name]

    # Filename heuristics
    base = file_name.lower()
    if base == "manifest.json":
        return "manifest"
    if "_transcript" in base:
        return "transcript"
    if "_packet" in base:
        return "context_packet"
    if base.startswith("decision_"):
        return "decision"

    return "unknown"


# ═════════════════════════════════════════════════════════════════════════
# Drive query helpers
# ═════════════════════════════════════════════════════════════════════════


def _list_files_in_folder(
    token: GDriveToken,
    folder_id: str,
    *,
    mime_type: str | None = None,
    query_extra: str = "",
    timeout: float = DEFAULT_REQUEST_TIMEOUT,
) -> list[dict]:
    """List all files in a Google Drive folder.

    Args:
        token: Valid ``GDriveToken``.
        folder_id: Drive folder ID to list.
        mime_type: Optional MIME type filter.
        query_extra: Additional query clauses (joined with 'and').
        timeout: HTTP request timeout.

    Returns:
        List of file metadata dicts from the Drive API.

    Raises:
        OSError: On network failure.
    """
    api_fn = _get_drive_api_fn()
    query_parts = [
        f"'{folder_id}' in parents",
        "trashed=false",
    ]
    if mime_type:
        query_parts.append(f"mimeType='{mime_type}'")
    if query_extra:
        query_parts.append(query_extra)

    query = " and ".join(query_parts)

    url = (
        f"{DRIVE_API_BASE}/files"
        f"?q={_url_encode(query)}"
        f"&fields=files(id,name,mimeType,webViewLink,parents)"
        f"&pageSize=100"
    )

    headers = {
        "Authorization": _build_auth_header(token),
        "Accept": "application/json",
    }

    status, response = api_fn("GET", url, headers, None, timeout)

    if status >= 400:
        raise ValueError(
            f"Failed to list files in folder '{folder_id}': "
            f"HTTP {status}: "
            f"{response.get('error', {}).get('message', 'unknown')}"
        )

    return response.get("files", [])


def _download_file_content(
    token: GDriveToken,
    file_id: str,
    *,
    timeout: float = DEFAULT_REQUEST_TIMEOUT,
) -> tuple[str, int]:
    """Download file content from Google Drive.

    Args:
        token: Valid ``GDriveToken``.
        file_id: Drive file ID to download.
        timeout: HTTP request timeout.

    Returns:
        ``(content_string, http_status)`` tuple.

    Raises:
        OSError: On network failure.
    """
    api_fn = _get_drive_api_fn()

    url = f"{DRIVE_API_BASE}/files/{file_id}?alt=media"

    headers = {
        "Authorization": _build_auth_header(token),
        "Accept": "*/*",
    }

    status, response = api_fn("GET", url, headers, None, timeout)

    if status >= 400:
        error_msg = response.get("error", {}).get("message", f"HTTP {status}")
        raise ValueError(f"Failed to download file '{file_id}': {error_msg}")

    # When downloading via ?alt=media, the response body is the raw content,
    # but our API handler returns a dict. The mock is expected to return the
    # content in a special "_content" key.
    if isinstance(response, dict) and "_content" in response:
        return (response["_content"], status)
    if isinstance(response, str):
        return (response, status)

    return (json.dumps(response, ensure_ascii=False), status)


# ═════════════════════════════════════════════════════════════════════════
# Folder resolution
# ═════════════════════════════════════════════════════════════════════════


def _resolve_artifact_folder(
    token: GDriveToken,
    meeting_id: str,
    artifact_type: str,
    *,
    root_folder_name: str = DEFAULT_ROOT_FOLDER_NAME,
    timeout: float = DEFAULT_REQUEST_TIMEOUT,
) -> str:
    """Resolve the Drive folder ID for a given meeting+artifact_type.

    Walks the hierarchy: root -> meeting_id -> artifact_sub_folder.

    Returns:
        The folder ID, or empty string if not found.
    """
    # Find root folder
    root = _find_folder(token, root_folder_name, timeout=timeout)
    if root is None:
        return ""

    # Find meeting folder
    meeting = _find_folder(token, meeting_id, parent_id=root.folder_id, timeout=timeout)
    if meeting is None:
        return ""

    # Manifest is at meeting folder root
    if artifact_type == "manifest":
        return meeting.folder_id

    # Map artifact type to sub-folder name
    type_to_sub = {
        "transcript": "transcripts",
        "context_packet": "context_packets",
        "decision": "decisions",
    }
    sub_name = type_to_sub.get(artifact_type)
    if sub_name is None:
        return ""

    sub = _find_folder(token, sub_name, parent_id=meeting.folder_id, timeout=timeout)
    if sub is None:
        return ""

    return sub.folder_id


# ═════════════════════════════════════════════════════════════════════════
# Core query functions
# ═════════════════════════════════════════════════════════════════════════


def find_artifacts(
    token: GDriveToken,
    meeting_id: str,
    artifact_type: str,
    *,
    round_number: int | None = None,
    role_id: str | None = None,
    decision_index: int | None = None,
    root_folder_name: str = DEFAULT_ROOT_FOLDER_NAME,
    timeout: float = DEFAULT_REQUEST_TIMEOUT,
) -> list[ArtifactInfo]:
    """Find artifacts of a given type in a meeting folder.

    Args:
        token: Valid ``GDriveToken``.
        meeting_id: Meeting identifier.
        artifact_type: One of transcript, context_packet, decision, manifest.
        round_number: Optional round filter (1–4).
        role_id: Optional role filter.
        decision_index: Optional decision index filter.
        root_folder_name: Top-level folder name.
        timeout: HTTP request timeout.

    Returns:
        List of ``ArtifactInfo`` matching the query.  Empty list if the
        folder hierarchy does not exist or no files match.

    Raises:
        ValueError: On invalid arguments.
        OSError: On network failure.
    """
    if not meeting_id.strip():
        raise ValueError("meeting_id must not be empty")
    if artifact_type not in MIME_TYPES:
        raise ValueError(
            f"artifact_type must be one of {list(MIME_TYPES)}, got '{artifact_type}'"
        )

    folder_id = _resolve_artifact_folder(
        token=token,
        meeting_id=meeting_id,
        artifact_type=artifact_type,
        root_folder_name=root_folder_name,
        timeout=timeout,
    )

    if not folder_id:
        return []

    # Build extra query filters
    query_extra_parts: list[str] = []

    # Name-based filtering for manifest
    if artifact_type == "manifest":
        query_extra_parts.append("name='manifest.json'")

    # Name-based filtering for decisions
    if artifact_type == "decision" and decision_index is not None:
        query_extra_parts.append(f"name='decision_{decision_index:03d}.json'")

    query_extra = " and ".join(query_extra_parts)

    try:
        files = _list_files_in_folder(
            token=token,
            folder_id=folder_id,
            mime_type=MIME_TYPES[artifact_type]
            if artifact_type != "manifest"
            else None,
            query_extra=query_extra,
            timeout=timeout,
        )
    except ValueError:
        return []

    results: list[ArtifactInfo] = []
    for f in files:
        parents = f.get("parents", [])
        info = ArtifactInfo(
            file_id=f.get("id", ""),
            file_name=f.get("name", ""),
            mime_type=f.get("mimeType", ""),
            meeting_id=meeting_id,
            artifact_type=artifact_type,
            web_view_link=f.get("webViewLink", ""),
            folder_id=parents[0] if parents else folder_id,
        )

        # Parse round/role from filename
        if artifact_type in ("transcript", "context_packet"):
            round_num, parsed_role = _parse_transcript_filename(info.file_name)
            info = ArtifactInfo(
                file_id=info.file_id,
                file_name=info.file_name,
                mime_type=info.mime_type,
                meeting_id=info.meeting_id,
                artifact_type=info.artifact_type,
                web_view_link=info.web_view_link,
                folder_id=info.folder_id,
                round_number=round_num,
                role_id=parsed_role or "",
            )
        elif artifact_type == "decision":
            dec_idx = _parse_decision_filename(info.file_name)
            info = ArtifactInfo(
                file_id=info.file_id,
                file_name=info.file_name,
                mime_type=info.mime_type,
                meeting_id=info.meeting_id,
                artifact_type=info.artifact_type,
                web_view_link=info.web_view_link,
                folder_id=info.folder_id,
                decision_index=dec_idx,
            )

        # Client-side filtering for round/role
        if round_number is not None and info.round_number != round_number:
            continue
        if role_id and info.role_id.lower() != role_id.lower():
            continue

        results.append(info)

    return results


def read_artifact_content(
    token: GDriveToken,
    file_id: str,
    *,
    timeout: float = DEFAULT_REQUEST_TIMEOUT,
) -> ArtifactReadResult:
    """Download the content of a single artifact from Google Drive.

    Args:
        token: Valid ``GDriveToken``.
        file_id: Drive file ID of the artifact.
        timeout: HTTP request timeout.

    Returns:
        ``ArtifactReadResult`` with content on success, error on failure.
    """
    if not file_id.strip():
        return ArtifactReadResult(
            success=False,
            error="file_id is required",
        )

    try:
        content, status = _download_file_content(token, file_id, timeout=timeout)
    except OSError:
        return ArtifactReadResult(
            success=False,
            error="network_error_reading_file",
        )
    except ValueError as exc:
        return ArtifactReadResult(
            success=False,
            error=str(exc),
            http_status=getattr(exc, "http_status", 0),
        )

    return ArtifactReadResult(
        success=True,
        content=content,
        http_status=status,
    )


def query_and_download(
    token: GDriveToken,
    query: ArtifactQuery,
    *,
    root_folder_name: str = DEFAULT_ROOT_FOLDER_NAME,
    timeout: float = DEFAULT_REQUEST_TIMEOUT,
) -> list[ArtifactReadResult]:
    """Query for artifacts and download their contents in one step.

    Combines ``find_artifacts`` and ``read_artifact_content``.  Each
    matching artifact is downloaded, yielding one ``ArtifactReadResult``
    per file.

    Args:
        token: Valid ``GDriveToken``.
        query: ``ArtifactQuery`` specifying meeting, type, and optional filters.
        root_folder_name: Top-level folder name.
        timeout: HTTP request timeout.

    Returns:
        List of ``ArtifactReadResult``, one per matching artifact.
        Empty list when no matching files are found.
    """
    if query.artifact_type is None:
        # Query all types
        all_infos: list[ArtifactInfo] = []
        for atype in MIME_TYPES:
            all_infos.extend(
                find_artifacts(
                    token=token,
                    meeting_id=query.meeting_id,
                    artifact_type=atype,
                    round_number=query.round_number,
                    role_id=query.role_id,
                    decision_index=query.decision_index,
                    root_folder_name=root_folder_name,
                    timeout=timeout,
                )
            )
    else:
        all_infos = find_artifacts(
            token=token,
            meeting_id=query.meeting_id,
            artifact_type=query.artifact_type,
            round_number=query.round_number,
            role_id=query.role_id,
            decision_index=query.decision_index,
            root_folder_name=root_folder_name,
            timeout=timeout,
        )

    results: list[ArtifactReadResult] = []
    for info in all_infos:
        result = read_artifact_content(token, info.file_id, timeout=timeout)
        # Decorate with the artifact info
        result = ArtifactReadResult(
            success=result.success,
            info=info,
            content=result.content,
            error=result.error,
            http_status=result.http_status,
            execution_id=result.execution_id,
        )
        results.append(result)

    return results


# ═════════════════════════════════════════════════════════════════════════
# ArtifactReader — high-level API
# ═════════════════════════════════════════════════════════════════════════


class ArtifactReader:
    """High-level reader for meeting artifacts stored in Google Drive.

    Mirrors the ``GDriveArtifactWriter`` pattern: manages folder resolution,
    artifact listing, and content download with a configurable root folder.

    Usage::

        config = ReaderConfig(root_folder_name="AI_Agent_Meetings")
        reader = ArtifactReader(config)

        token = auth.get_valid_token().token

        # List all transcripts for a meeting
        infos = reader.query_artifacts(
            token=token,
            meeting_id="meeting_20260610_abc123",
            artifact_type="transcript",
        )

        # Download one
        result = reader.download_artifact(token=token, file_id=infos[0].file_id)
        print(result.content)

        # Query and download all decisions in one call
        results = reader.query_and_download(
            token=token,
            meeting_id="meeting_20260610_abc123",
            artifact_type="decision",
        )
    """

    def __init__(self, config: ReaderConfig | None = None) -> None:
        self._config = config or ReaderConfig()
        self._folder_cache: dict[str, str] = {}

    @property
    def config(self) -> ReaderConfig:
        return self._config

    # ── Artifact listing ───────────────────────────────────────────────

    def query_artifacts(
        self,
        token: GDriveToken,
        meeting_id: str,
        artifact_type: str,
        *,
        round_number: int | None = None,
        role_id: str | None = None,
        decision_index: int | None = None,
        timeout: float | None = None,
    ) -> list[ArtifactInfo]:
        """List artifacts of a given type in a meeting folder.

        Args:
            token: Valid ``GDriveToken``.
            meeting_id: Meeting identifier.
            artifact_type: One of transcript, context_packet, decision, manifest.
            round_number: Optional round filter.
            role_id: Optional role filter.
            decision_index: Optional decision index filter.
            timeout: Optional per-call timeout override.

        Returns:
            List of ``ArtifactInfo``.  Empty if none found.
        """
        effective_timeout = timeout or self._config.request_timeout
        return find_artifacts(
            token=token,
            meeting_id=meeting_id,
            artifact_type=artifact_type,
            round_number=round_number,
            role_id=role_id,
            decision_index=decision_index,
            root_folder_name=self._config.root_folder_name,
            timeout=effective_timeout,
        )

    # ── Content download ───────────────────────────────────────────────

    def download_artifact(
        self,
        token: GDriveToken,
        file_id: str,
        *,
        timeout: float | None = None,
    ) -> ArtifactReadResult:
        """Download a single artifact's content.

        Args:
            token: Valid ``GDriveToken``.
            file_id: Drive file ID (from an ``ArtifactInfo``).
            timeout: Optional per-call timeout override.

        Returns:
            ``ArtifactReadResult`` with content string on success.
        """
        effective_timeout = timeout or self._config.request_timeout
        return read_artifact_content(token, file_id, timeout=effective_timeout)

    # ── Combined query + download ──────────────────────────────────────

    def query_and_download(
        self,
        token: GDriveToken,
        meeting_id: str,
        artifact_type: str,
        *,
        round_number: int | None = None,
        role_id: str | None = None,
        decision_index: int | None = None,
        timeout: float | None = None,
    ) -> list[ArtifactReadResult]:
        """List artifacts and download all matching contents.

        Args:
            token: Valid ``GDriveToken``.
            meeting_id: Meeting identifier.
            artifact_type: One of transcript, context_packet, decision, manifest.
            round_number: Optional round filter.
            role_id: Optional role filter.
            decision_index: Optional decision index filter.
            timeout: Optional per-call timeout override.

        Returns:
            List of ``ArtifactReadResult``, one per matching artifact.
            Each result includes ``info`` (metadata) and ``content`` (the
            downloaded file body).
        """
        effective_timeout = timeout or self._config.request_timeout
        query = ArtifactQuery(
            meeting_id=meeting_id,
            artifact_type=artifact_type,
            round_number=round_number,
            role_id=role_id,
            decision_index=decision_index,
        )
        return query_and_download(
            token=token,
            query=query,
            root_folder_name=self._config.root_folder_name,
            timeout=effective_timeout,
        )

    # ── Batch query (all types) ────────────────────────────────────────

    def query_all_artifacts(
        self,
        token: GDriveToken,
        meeting_id: str,
        *,
        round_number: int | None = None,
        role_id: str | None = None,
        timeout: float | None = None,
    ) -> dict[str, list[ArtifactInfo]]:
        """Query all artifact types for a meeting in one call.

        Args:
            token: Valid ``GDriveToken``.
            meeting_id: Meeting identifier.
            round_number: Optional round filter (applied to transcripts
                and context_packets only).
            role_id: Optional role filter (applied to transcripts and
                context_packets only).
            timeout: Optional per-call timeout override.

        Returns:
            Dict mapping artifact type name to list of ``ArtifactInfo``.
            Keys present: transcript, context_packet, decision, manifest.
        """
        effective_timeout = timeout or self._config.request_timeout
        result: dict[str, list[ArtifactInfo]] = {}

        for atype in MIME_TYPES:
            # Round and role filters apply only to transcript/context_packet
            if atype in ("transcript", "context_packet"):
                result[atype] = self.query_artifacts(
                    token=token,
                    meeting_id=meeting_id,
                    artifact_type=atype,
                    round_number=round_number,
                    role_id=role_id,
                    timeout=effective_timeout,
                )
            else:
                result[atype] = self.query_artifacts(
                    token=token,
                    meeting_id=meeting_id,
                    artifact_type=atype,
                    timeout=effective_timeout,
                )

        return result

    # ── Cache management ───────────────────────────────────────────────

    def clear_folder_cache(self) -> None:
        """Clear the internal folder ID cache."""
        self._folder_cache.clear()


# ═════════════════════════════════════════════════════════════════════════
# Module-level convenience
# ═════════════════════════════════════════════════════════════════════════


def build_reader(
    root_folder_name: str = DEFAULT_ROOT_FOLDER_NAME,
    request_timeout: float = DEFAULT_REQUEST_TIMEOUT,
) -> ArtifactReader:
    """Build an ``ArtifactReader`` from individual parameters.

    Args:
        root_folder_name: Top-level folder name in Google Drive.
        request_timeout: HTTP request timeout in seconds.

    Returns:
        A configured ``ArtifactReader`` instance.
    """
    config = ReaderConfig(
        root_folder_name=root_folder_name,
        request_timeout=request_timeout,
    )
    return ArtifactReader(config)
