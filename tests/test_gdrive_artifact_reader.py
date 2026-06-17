"""Tests for the artifact reader module (Sub-AC 19a-3).

Verifies:
- ReaderConfig validation
- ArtifactInfo, ArtifactReadResult, ArtifactQuery dataclass creation
- ArtifactType enum values (re-exported from writer)
- _parse_transcript_filename and _parse_decision_filename helpers
- _infer_artifact_type helper
- Mock Drive API listing (find artifacts in folder hierarchy)
- Mock file download
- find_artifacts with type/round/role/decision filters
- read_artifact_content — success and error paths
- query_and_download combined query+download flow
- ArtifactReader high-level API:
  - query_artifacts (per type)
  - download_artifact (by file ID)
  - query_and_download (combined)
  - query_all_artifacts (all types)
- Error paths:
  - missing folder hierarchy
  - empty file_id on download
  - HTTP errors from mock Drive API
  - network failures
  - invalid artifact_type
  - empty meeting_id
- Edge cases:
  - folder names with special characters
  - Unicode content download
  - Empty content
  - No matching files (empty result)
  - Query across all types (multi-type)

Uses the same ``inject_drive_api()`` mechanism as ``gdrive_artifact_writer``
so no real Google Drive calls are made during tests.
"""

from __future__ import annotations

import json
import time
from typing import Any

import pytest

from src.gdrive_artifact_writer import (
    DEFAULT_ROOT_FOLDER_NAME,
    DRIVE_API_BASE,
    FOLDER_MIME_TYPE,
    MIME_TYPES,
    ArtifactType,
    GDriveFolder,
    _find_folder,
    inject_drive_api,
)
from src.gdrive_artifact_reader import (
    ArtifactInfo,
    ArtifactQuery,
    ArtifactReadResult,
    ArtifactReader,
    ReaderConfig,
    _download_file_content,
    _infer_artifact_type,
    _list_files_in_folder,
    _parse_decision_filename,
    _parse_transcript_filename,
    _resolve_artifact_folder,
    build_reader,
    find_artifacts,
    query_and_download,
    read_artifact_content,
)
from src.gdrive_auth import GDriveToken


# ═════════════════════════════════════════════════════════════════════════
# Helpers
# ═════════════════════════════════════════════════════════════════════════


def _make_token(
    access_token: str = "ya29.test-reader-token",
    token_type: str = "Bearer",
    expires_in: float = 3600.0,
) -> GDriveToken:
    """Build a valid, non-expired GDriveToken for test use."""
    return GDriveToken(
        access_token=access_token,
        refresh_token="1//test-refresh-reader",
        token_type=token_type,
        expires_at=time.time() + expires_in,
        scope="https://www.googleapis.com/auth/drive.readonly",
    )


def _make_config(
    root_folder_name: str = DEFAULT_ROOT_FOLDER_NAME,
    request_timeout: float = 10.0,
) -> ReaderConfig:
    """Build a test ReaderConfig."""
    return ReaderConfig(
        root_folder_name=root_folder_name,
        request_timeout=request_timeout,
    )


# ── Mock Drive API handlers ──────────────────────────────────────────────


class MockReadDriveAPI:
    """Stateful mock Google Drive API for reader tests.

    Maintains an in-memory file/folder store so tests can verify
    listing and download operations.  Pre-seeded with a standard
    meeting folder hierarchy.
    """

    def __init__(self, seed_hierarchy: bool = True) -> None:
        self._store: dict[str, dict] = {}  # file_id -> metadata
        self._next_id: int = 10000
        self._call_log: list[dict] = []

        if seed_hierarchy:
            self._seed_default_hierarchy()

    def _new_id(self) -> str:
        self._next_id += 1
        return f"mock_file_{self._next_id}"

    def _add_entry(self, entry: dict) -> dict:
        fid = self._new_id()
        entry["id"] = fid
        self._store[fid] = {**entry}
        return entry

    def _add_file_with_content(self, entry: dict, content: str) -> dict:
        fid = self._new_id()
        entry["id"] = fid
        entry["_content"] = content
        self._store[fid] = {**entry}
        return entry

    def _seed_default_hierarchy(self) -> None:
        """Create standard meeting folder hierarchy with sample files.

        Structure::

            AI_Agent_Meetings/                  (root — matches DEFAULT_ROOT_FOLDER_NAME)
              meeting_20260610_abc123/            (meeting)
                transcripts/
                  round_1_transcript.md           "R1 transcript"
                  round_1_ceo_transcript.md       "CEO R1 notes"
                  round_2_transcript.md           "R2 transcript"
                context_packets/
                  round_1_role_ceo_packet.json    '{"role":"ceo"}'
                  round_2_role_cto_packet.json    '{"role":"cto"}'
                decisions/
                  decision_001.json               '{"decision":"hire"}'
                  decision_002.json               '{"decision":"budget"}'
                manifest.json                     '{"meeting_id":"..."}'
        """
        # Root folder
        root = self._add_entry({
            "name": DEFAULT_ROOT_FOLDER_NAME,
            "mimeType": FOLDER_MIME_TYPE,
            "parents": [],
            "trashed": False,
        })
        root_id = root["id"]

        # Meeting folder
        meeting = self._add_entry({
            "name": "meeting_20260610_abc123",
            "mimeType": FOLDER_MIME_TYPE,
            "parents": [root_id],
            "trashed": False,
        })
        meeting_id = meeting["id"]

        # Sub-folders
        transcripts_folder = self._add_entry({
            "name": "transcripts",
            "mimeType": FOLDER_MIME_TYPE,
            "parents": [meeting_id],
            "trashed": False,
        })
        t_folder_id = transcripts_folder["id"]

        packets_folder = self._add_entry({
            "name": "context_packets",
            "mimeType": FOLDER_MIME_TYPE,
            "parents": [meeting_id],
            "trashed": False,
        })
        p_folder_id = packets_folder["id"]

        decisions_folder = self._add_entry({
            "name": "decisions",
            "mimeType": FOLDER_MIME_TYPE,
            "parents": [meeting_id],
            "trashed": False,
        })
        d_folder_id = decisions_folder["id"]

        # Transcripts
        self._add_file_with_content({
            "name": "round_1_transcript.md",
            "mimeType": "text/markdown",
            "parents": [t_folder_id],
            "webViewLink": "https://drive.google.com/file/d/r1t/view",
            "trashed": False,
        }, "# Round 1 Transcript\n\nDiscussion about strategy.")

        self._add_file_with_content({
            "name": "round_1_ceo_transcript.md",
            "mimeType": "text/markdown",
            "parents": [t_folder_id],
            "webViewLink": "https://drive.google.com/file/d/r1c/view",
            "trashed": False,
        }, "# CEO Round 1 Notes\n\nBudget approval needed.")

        self._add_file_with_content({
            "name": "round_2_transcript.md",
            "mimeType": "text/markdown",
            "parents": [t_folder_id],
            "webViewLink": "https://drive.google.com/file/d/r2t/view",
            "trashed": False,
        }, "# Round 2 Transcript\n\nFollow-up on action items.")

        # Context packets
        self._add_file_with_content({
            "name": "round_1_role_ceo_packet.json",
            "mimeType": "application/json",
            "parents": [p_folder_id],
            "webViewLink": "https://drive.google.com/file/d/r1cp/view",
            "trashed": False,
        }, '{"role": "ceo", "round": 1, "opinion": "invest in AI"}')

        self._add_file_with_content({
            "name": "round_2_role_cto_packet.json",
            "mimeType": "application/json",
            "parents": [p_folder_id],
            "webViewLink": "https://drive.google.com/file/d/r2cp/view",
            "trashed": False,
        }, '{"role": "cto", "round": 2, "opinion": "upgrade infra"}')

        # Decisions
        self._add_file_with_content({
            "name": "decision_001.json",
            "mimeType": "application/json",
            "parents": [d_folder_id],
            "webViewLink": "https://drive.google.com/file/d/d001/view",
            "trashed": False,
        }, '{"decision": "hire", "approved": true}')

        self._add_file_with_content({
            "name": "decision_002.json",
            "mimeType": "application/json",
            "parents": [d_folder_id],
            "webViewLink": "https://drive.google.com/file/d/d002/view",
            "trashed": False,
        }, '{"decision": "budget", "approved": true}')

        # Manifest
        self._add_file_with_content({
            "name": "manifest.json",
            "mimeType": "application/json",
            "parents": [meeting_id],
            "webViewLink": "https://drive.google.com/file/d/manifest/view",
            "trashed": False,
        }, json.dumps({
            "meeting_id": "meeting_20260610_abc123",
            "state": "completed",
        }))

    def _search(self, query_str: str) -> list[dict]:
        """Mock search: supports name=, mimeType=, trashed=, 'parent' in parents."""
        from urllib.parse import unquote

        query_str = unquote(query_str)
        results = list(self._store.values())

        parts = self._split_query(query_str)

        for part in parts:
            part = part.strip()
            if not part:
                continue

            if " in parents" in part:
                pid = part.split(" in parents")[0].strip().strip("'")
                results = [
                    r for r in results
                    if pid in r.get("parents", [])
                ]
                continue

            if "=" not in part:
                continue

            key, val = part.split("=", 1)
            key = key.strip()
            val = val.strip().strip("'")

            if key == "trashed":
                want_trashed = val.lower() == "true"
                results = [
                    r for r in results
                    if bool(r.get("trashed", False)) == want_trashed
                ]
            else:
                results = [
                    r for r in results
                    if str(r.get(key, "")) == val
                ]

        return results

    @staticmethod
    def _split_query(query_str: str) -> list[str]:
        """Split a Drive API query string on ' and '."""
        parts = []
        current = ""
        in_expr = 0
        i = 0
        while i < len(query_str):
            if (
                in_expr == 0
                and i + 4 < len(query_str)
                and query_str[i : i + 5] == " and "
            ):
                parts.append(current)
                current = ""
                i += 5
                continue
            if query_str[i] == "(":
                in_expr += 1
            elif query_str[i] == ")":
                in_expr = max(0, in_expr - 1)
            current += query_str[i]
            i += 1
        if current:
            parts.append(current)
        return parts

    def handle(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None = None,
        timeout: float = 10.0,
    ) -> tuple[int, dict]:
        """Main mock handler matching the inject_drive_api signature."""
        self._call_log.append({
            "method": method,
            "url": url,
            "headers": dict(headers),
            "body": body,
        })

        # ── Authorization check ──────────────────────────────────────
        auth = headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return (401, {"error": {"message": "Unauthorized"}})

        # ── File download (GET /files/{id}?alt=media) ────────────────
        if method == "GET" and "?alt=media" in url:
            file_id = url.split("/files/")[1].split("?")[0]
            entry = self._store.get(file_id)
            if entry is None:
                return (404, {"error": {"message": f"File not found: {file_id}"}})
            content = entry.get("_content", "")
            return (200, {"_content": content})

        # ── Folder/file listing (GET /files?q=...) ───────────────────
        if method == "GET" and DRIVE_API_BASE in url and "/files" in url:
            query_str = ""
            if "?q=" in url:
                q_start = url.index("?q=") + 3
                q_end = url.index("&", q_start) if "&" in url[q_start:] else len(url)
                query_str = url[q_start:q_end]

            files = self._search(query_str)
            return (200, {"files": files[:100]})

        # ── Folder create (POST /files) ──────────────────────────────
        if method == "POST" and DRIVE_API_BASE in url and "/files" in url:
            if body is None:
                return (400, {"error": {"message": "Missing body"}})
            metadata = json.loads(body)
            name = metadata.get("name", "unnamed")
            entry = self._add_entry({
                "name": name,
                "mimeType": metadata.get("mimeType", "application/octet-stream"),
                "parents": metadata.get("parents", []),
                "trashed": False,
            })
            return (200, {
                "id": entry["id"],
                "name": entry["name"],
                "parents": entry["parents"],
            })

        # ── File upload (POST /upload/drive/v3/files) ────────────────
        if method == "POST" and "upload/drive" in url and "/files" in url:
            if body is None:
                return (400, {"error": {"message": "Missing body"}})

            body_str = body.decode("utf-8")

            # Parse multipart/related body using the boundary
            metadata_json = ""
            extracted_content = ""

            # Extract boundary from Content-Type header or infer from body
            content_type = headers.get("Content-Type", "")
            boundary = ""
            if "boundary=" in content_type:
                boundary = content_type.split("boundary=")[-1].strip()
            else:
                # Infer: first line starts with --boundary
                first_line = body_str.split("\r\n")[0]
                if first_line.startswith("--"):
                    boundary = first_line[2:]

            if boundary:
                # Split on boundary markers
                sections = body_str.split(f"--{boundary}")
                for section in sections:
                    section = section.strip()
                    if not section or section == "--":
                        continue
                    # Separate headers from body
                    parts = section.split("\r\n\r\n", 1)
                    if len(parts) < 2:
                        continue
                    header_part, content_part = parts[0], parts[1]
                    if "application/json" in header_part and "charset=UTF-8" in header_part:
                        # This is the metadata part
                        try:
                            json.loads(content_part.strip())
                            metadata_json = content_part.strip()
                        except json.JSONDecodeError:
                            pass
                    else:
                        # This is the content part
                        extracted_content = content_part.strip()
            else:
                # Fallback: simple line-based parsing
                for line in body_str.split("\r\n"):
                    if line.startswith("{") and line.endswith("}"):
                        try:
                            json.loads(line)
                            if not metadata_json:
                                metadata_json = line
                            else:
                                extracted_content = line
                        except json.JSONDecodeError:
                            continue

            if not metadata_json:
                return (400, {"error": {"message": "Missing metadata in multipart"}})

            metadata = json.loads(metadata_json)
            name = metadata.get("name", "unnamed")
            mime_type = metadata.get("mimeType", "application/octet-stream")
            parents = metadata.get("parents", [])

            entry = self._add_entry({
                "name": name,
                "mimeType": mime_type,
                "parents": parents,
                "webViewLink": f"https://drive.google.com/file/d/{self._next_id + 1}/view",
                "trashed": False,
                "_content": extracted_content,
            })
            return (200, {
                "id": entry["id"],
                "name": entry["name"],
                "mimeType": entry["mimeType"],
                "webViewLink": entry["webViewLink"],
                "parents": entry["parents"],
            })

        # ── Fallback ─────────────────────────────────────────────────
        msg = f"Unknown mock endpoint: {method} {url}"
        return (404, {"error": {"message": msg}})


def _mock_drive_network_error(
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes | None = None,
    timeout: float = 10.0,
) -> tuple[int, dict]:
    """Mock that raises a network error."""
    raise OSError("Connection reset by peer")


def _mock_drive_auth_error(
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes | None = None,
    timeout: float = 10.0,
) -> tuple[int, dict]:
    """Mock that returns 401 unauthorized."""
    return (401, {"error": {"message": "Invalid Credentials"}})


def _mock_drive_server_error(
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes | None = None,
    timeout: float = 10.0,
) -> tuple[int, dict]:
    """Mock that returns 500 server error."""
    return (500, {"error": {"message": "Internal server error"}})


# ── Fixture: auto-reset inject ──────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_drive_api() -> Any:
    """Reset the Drive API inject to default after each test."""
    yield
    inject_drive_api(None)


# ═════════════════════════════════════════════════════════════════════════
# ReaderConfig tests
# ═════════════════════════════════════════════════════════════════════════


class TestReaderConfig:
    """Verify ReaderConfig creation and validation."""

    def test_default_config(self) -> None:
        config = ReaderConfig()
        assert config.root_folder_name == DEFAULT_ROOT_FOLDER_NAME
        assert config.request_timeout == 30.0

    def test_custom_config(self) -> None:
        config = ReaderConfig(
            root_folder_name="Custom_Meetings",
            request_timeout=45.0,
        )
        assert config.root_folder_name == "Custom_Meetings"
        assert config.request_timeout == 45.0

    def test_empty_root_folder_name_raises(self) -> None:
        with pytest.raises(ValueError, match="root_folder_name"):
            ReaderConfig(root_folder_name="")

    def test_whitespace_root_folder_name_raises(self) -> None:
        with pytest.raises(ValueError, match="root_folder_name"):
            ReaderConfig(root_folder_name="   ")

    def test_zero_timeout_raises(self) -> None:
        with pytest.raises(ValueError, match="request_timeout"):
            ReaderConfig(request_timeout=0)

    def test_negative_timeout_raises(self) -> None:
        with pytest.raises(ValueError, match="request_timeout"):
            ReaderConfig(request_timeout=-5.0)


# ═════════════════════════════════════════════════════════════════════════
# Data type tests
# ═════════════════════════════════════════════════════════════════════════


class TestDataTypes:
    """Verify ArtifactInfo, ArtifactReadResult, ArtifactQuery."""

    def test_artifact_info_creation(self) -> None:
        info = ArtifactInfo(
            file_id="f123",
            file_name="round_1_transcript.md",
            mime_type="text/markdown",
            meeting_id="meeting_abc",
            artifact_type="transcript",
            web_view_link="https://example.com",
            folder_id="folder_1",
            round_number=1,
            role_id="",
            decision_index=0,
        )
        assert info.file_id == "f123"
        assert info.round_number == 1
        assert info.artifact_type == "transcript"

    def test_artifact_info_defaults(self) -> None:
        info = ArtifactInfo(file_id="f1", file_name="test.md", mime_type="text/plain")
        assert info.meeting_id == ""
        assert info.artifact_type == ""
        assert info.round_number == 0
        assert info.role_id == ""
        assert info.decision_index == 0

    def test_artifact_read_result_success(self) -> None:
        info = ArtifactInfo(file_id="f1", file_name="test.json", mime_type="application/json")
        result = ArtifactReadResult(success=True, info=info, content='{"key":"val"}')
        assert result.success is True
        assert result.info is not None
        assert result.content == '{"key":"val"}'
        assert result.error == ""

    def test_artifact_read_result_failure(self) -> None:
        result = ArtifactReadResult(
            success=False,
            error="File not found",
            http_status=404,
        )
        assert result.success is False
        assert result.info is None
        assert result.content is None
        assert result.error == "File not found"
        assert result.http_status == 404

    def test_artifact_read_result_with_execution_id(self) -> None:
        result = ArtifactReadResult(
            success=True,
            execution_id="exec_001",
        )
        assert result.execution_id == "exec_001"

    def test_artifact_query_creation(self) -> None:
        query = ArtifactQuery(
            meeting_id="meeting_abc",
            artifact_type="transcript",
            round_number=2,
            role_id="ceo",
        )
        assert query.meeting_id == "meeting_abc"
        assert query.artifact_type == "transcript"
        assert query.round_number == 2
        assert query.role_id == "ceo"
        assert query.decision_index is None

    def test_artifact_query_all_types(self) -> None:
        query = ArtifactQuery(meeting_id="meeting_abc", artifact_type=None)
        assert query.artifact_type is None

    def test_artifact_query_empty_meeting_id_raises(self) -> None:
        with pytest.raises(ValueError, match="meeting_id"):
            ArtifactQuery(meeting_id="")

    def test_artifact_query_invalid_type_raises(self) -> None:
        with pytest.raises(ValueError, match="artifact_type"):
            ArtifactQuery(meeting_id="m1", artifact_type="invalid_type")


# ═════════════════════════════════════════════════════════════════════════
# Filename parsing tests
# ═════════════════════════════════════════════════════════════════════════


class TestParseTranscriptFilename:
    """Verify _parse_transcript_filename."""

    def test_round_only_transcript(self) -> None:
        r, role = _parse_transcript_filename("round_1_transcript.md")
        assert r == 1
        assert role == ""

    def test_round_with_role_transcript(self) -> None:
        r, role = _parse_transcript_filename("round_2_ceo_transcript.md")
        assert r == 2
        assert role == "ceo"

    def test_round_with_multi_part_role(self) -> None:
        r, role = _parse_transcript_filename("round_3_head_of_design_transcript.md")
        assert r == 3
        assert role == "head_of_design"

    def test_packet_with_role(self) -> None:
        r, role = _parse_transcript_filename("round_1_role_cto_packet.json")
        assert r == 1
        assert role == "cto"

    def test_unparseable_returns_zero(self) -> None:
        r, role = _parse_transcript_filename("manifest.json")
        assert r == 0
        assert role == ""


class TestParseDecisionFilename:
    """Verify _parse_decision_filename."""

    def test_decision_001(self) -> None:
        assert _parse_decision_filename("decision_001.json") == 1

    def test_decision_042(self) -> None:
        assert _parse_decision_filename("decision_042.json") == 42

    def test_unparseable_returns_zero(self) -> None:
        assert _parse_decision_filename("manifest.json") == 0


class TestInferArtifactType:
    """Verify _infer_artifact_type."""

    def test_transcript_by_folder(self) -> None:
        assert _infer_artifact_type("some_file.md", "transcripts") == "transcript"

    def test_context_packet_by_folder(self) -> None:
        assert _infer_artifact_type("round_1.json", "context_packets") == "context_packet"

    def test_decision_by_folder(self) -> None:
        assert _infer_artifact_type("output.json", "decisions") == "decision"

    def test_manifest_by_filename(self) -> None:
        assert _infer_artifact_type("manifest.json", "root") == "manifest"

    def test_transcript_by_filename(self) -> None:
        assert _infer_artifact_type("round_1_transcript.md") == "transcript"

    def test_packet_by_filename(self) -> None:
        assert _infer_artifact_type("round_1_role_ceo_packet.json") == "context_packet"

    def test_decision_by_filename(self) -> None:
        assert _infer_artifact_type("decision_005.json") == "decision"

    def test_unknown(self) -> None:
        assert _infer_artifact_type("random_file.txt") == "unknown"


# ═════════════════════════════════════════════════════════════════════════
# Mock-integrated listing & download tests
# ═════════════════════════════════════════════════════════════════════════


class TestFindArtifacts:
    """Verify find_artifacts with mock Drive API."""

    def test_find_transcripts_all(self) -> None:
        mock = MockReadDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()

        results = find_artifacts(
            token=token,
            meeting_id="meeting_20260610_abc123",
            artifact_type="transcript",
        )

        assert len(results) == 3
        names = {r.file_name for r in results}
        assert "round_1_transcript.md" in names
        assert "round_1_ceo_transcript.md" in names
        assert "round_2_transcript.md" in names

    def test_find_transcripts_filter_by_round(self) -> None:
        mock = MockReadDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()

        results = find_artifacts(
            token=token,
            meeting_id="meeting_20260610_abc123",
            artifact_type="transcript",
            round_number=1,
        )

        assert len(results) == 2
        for r in results:
            assert r.round_number == 1

    def test_find_transcripts_filter_by_round_and_role(self) -> None:
        mock = MockReadDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()

        results = find_artifacts(
            token=token,
            meeting_id="meeting_20260610_abc123",
            artifact_type="transcript",
            round_number=1,
            role_id="ceo",
        )

        assert len(results) == 1
        assert results[0].file_name == "round_1_ceo_transcript.md"
        assert results[0].role_id == "ceo"

    def test_find_context_packets(self) -> None:
        mock = MockReadDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()

        results = find_artifacts(
            token=token,
            meeting_id="meeting_20260610_abc123",
            artifact_type="context_packet",
        )

        assert len(results) == 2
        for r in results:
            assert r.artifact_type == "context_packet"

    def test_find_decisions(self) -> None:
        mock = MockReadDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()

        results = find_artifacts(
            token=token,
            meeting_id="meeting_20260610_abc123",
            artifact_type="decision",
        )

        assert len(results) == 2
        for r in results:
            assert r.artifact_type == "decision"
        indices = {r.decision_index for r in results}
        assert 1 in indices
        assert 2 in indices

    def test_find_decision_by_index(self) -> None:
        mock = MockReadDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()

        results = find_artifacts(
            token=token,
            meeting_id="meeting_20260610_abc123",
            artifact_type="decision",
            decision_index=1,
        )

        assert len(results) == 1
        assert results[0].decision_index == 1

    def test_find_manifest(self) -> None:
        mock = MockReadDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()

        results = find_artifacts(
            token=token,
            meeting_id="meeting_20260610_abc123",
            artifact_type="manifest",
        )

        assert len(results) == 1
        assert results[0].file_name == "manifest.json"
        assert results[0].artifact_type == "manifest"

    def test_find_nonexistent_meeting(self) -> None:
        mock = MockReadDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()

        results = find_artifacts(
            token=token,
            meeting_id="meeting_nonexistent",
            artifact_type="transcript",
        )

        assert results == []

    def test_find_nonexistent_root_folder(self) -> None:
        mock = MockReadDriveAPI(seed_hierarchy=False)
        inject_drive_api(mock.handle)
        token = _make_token()

        results = find_artifacts(
            token=token,
            meeting_id="meeting_20260610_abc123",
            artifact_type="transcript",
        )

        assert results == []

    def test_find_invalid_artifact_type_raises(self) -> None:
        mock = MockReadDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()

        with pytest.raises(ValueError, match="artifact_type"):
            find_artifacts(
                token=token,
                meeting_id="meeting_20260610_abc123",
                artifact_type="invalid",
            )

    def test_find_empty_meeting_id_raises(self) -> None:
        mock = MockReadDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()

        with pytest.raises(ValueError, match="meeting_id"):
            find_artifacts(
                token=token,
                meeting_id="",
                artifact_type="transcript",
            )


class TestReadArtifactContent:
    """Verify read_artifact_content with mock Drive API."""

    def test_read_transcript_content(self) -> None:
        mock = MockReadDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()

        # Find the file first
        infos = find_artifacts(
            token=token,
            meeting_id="meeting_20260610_abc123",
            artifact_type="transcript",
            round_number=1,
        )
        r1 = [i for i in infos if i.file_name == "round_1_transcript.md"][0]

        result = read_artifact_content(token=token, file_id=r1.file_id)

        assert result.success is True
        assert result.content is not None
        assert "Round 1 Transcript" in result.content

    def test_read_json_artifact_content(self) -> None:
        mock = MockReadDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()

        infos = find_artifacts(
            token=token,
            meeting_id="meeting_20260610_abc123",
            artifact_type="decision",
        )
        d1 = [i for i in infos if i.decision_index == 1][0]

        result = read_artifact_content(token=token, file_id=d1.file_id)

        assert result.success is True
        assert result.content is not None
        parsed = json.loads(result.content)
        assert parsed["decision"] == "hire"

    def test_read_manifest_content(self) -> None:
        mock = MockReadDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()

        infos = find_artifacts(
            token=token,
            meeting_id="meeting_20260610_abc123",
            artifact_type="manifest",
        )

        result = read_artifact_content(token=token, file_id=infos[0].file_id)

        assert result.success is True
        assert result.content is not None
        parsed = json.loads(result.content)
        assert parsed["meeting_id"] == "meeting_20260610_abc123"

    def test_read_empty_file_id_returns_error(self) -> None:
        token = _make_token()

        result = read_artifact_content(token=token, file_id="")

        assert result.success is False
        assert "file_id" in result.error.lower()

    def test_read_nonexistent_file(self) -> None:
        mock = MockReadDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()

        result = read_artifact_content(token=token, file_id="nonexistent_id")

        assert result.success is False
        assert "File not found" in result.error or result.http_status == 404

    def test_read_network_error(self) -> None:
        inject_drive_api(_mock_drive_network_error)
        token = _make_token()

        result = read_artifact_content(token=token, file_id="some_file")

        assert result.success is False
        assert "network" in result.error.lower() or "connection" in result.error.lower()


class TestQueryAndDownload:
    """Verify query_and_download combined flow."""

    def test_query_and_download_transcripts(self) -> None:
        mock = MockReadDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()

        query = ArtifactQuery(
            meeting_id="meeting_20260610_abc123",
            artifact_type="transcript",
        )

        results = query_and_download(token=token, query=query)

        assert len(results) == 3
        for r in results:
            assert r.success is True
            assert r.info is not None
            assert r.content is not None

    def test_query_and_download_filtered(self) -> None:
        mock = MockReadDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()

        query = ArtifactQuery(
            meeting_id="meeting_20260610_abc123",
            artifact_type="context_packet",
            round_number=2,
            role_id="cto",
        )

        results = query_and_download(token=token, query=query)

        assert len(results) == 1
        assert results[0].info.role_id == "cto"
        assert results[0].info.round_number == 2

    def test_query_and_download_all_types(self) -> None:
        mock = MockReadDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()

        query = ArtifactQuery(
            meeting_id="meeting_20260610_abc123",
            artifact_type=None,
        )

        results = query_and_download(token=token, query=query)

        # Should find: 3 transcripts + 2 packets + 2 decisions + 1 manifest = 8
        assert len(results) == 8
        for r in results:
            assert r.success is True
            assert r.info is not None

    def test_query_and_download_empty_results(self) -> None:
        mock = MockReadDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()

        query = ArtifactQuery(
            meeting_id="meeting_20260610_abc123",
            artifact_type="transcript",
            round_number=99,
        )

        results = query_and_download(token=token, query=query)
        assert results == []


# ═════════════════════════════════════════════════════════════════════════
# ArtifactReader high-level API tests
# ═════════════════════════════════════════════════════════════════════════


class TestArtifactReaderQuery:
    """Verify ArtifactReader.query_artifacts."""

    def test_query_transcripts(self) -> None:
        mock = MockReadDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()
        reader = ArtifactReader(_make_config())

        results = reader.query_artifacts(
            token=token,
            meeting_id="meeting_20260610_abc123",
            artifact_type="transcript",
        )

        assert len(results) == 3

    def test_query_nonexistent_meeting(self) -> None:
        mock = MockReadDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()
        reader = ArtifactReader(_make_config())

        results = reader.query_artifacts(
            token=token,
            meeting_id="no_such_meeting",
            artifact_type="transcript",
        )

        assert results == []


class TestArtifactReaderDownload:
    """Verify ArtifactReader.download_artifact."""

    def test_download_by_file_id(self) -> None:
        mock = MockReadDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()
        reader = ArtifactReader(_make_config())

        # Get a known file ID
        infos = reader.query_artifacts(
            token=token,
            meeting_id="meeting_20260610_abc123",
            artifact_type="transcript",
        )
        r1 = [i for i in infos if i.round_number == 1 and i.role_id == ""][0]

        result = reader.download_artifact(token=token, file_id=r1.file_id)

        assert result.success is True
        assert "Round 1 Transcript" in (result.content or "")


class TestArtifactReaderQueryAndDownload:
    """Verify ArtifactReader.query_and_download."""

    def test_combined_flow(self) -> None:
        mock = MockReadDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()
        reader = ArtifactReader(_make_config())

        results = reader.query_and_download(
            token=token,
            meeting_id="meeting_20260610_abc123",
            artifact_type="decision",
        )

        assert len(results) == 2
        for r in results:
            assert r.success is True
            assert r.info is not None
            assert r.content is not None

    def test_combined_flow_manifest(self) -> None:
        mock = MockReadDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()
        reader = ArtifactReader(_make_config())

        results = reader.query_and_download(
            token=token,
            meeting_id="meeting_20260610_abc123",
            artifact_type="manifest",
        )

        assert len(results) == 1
        assert results[0].success is True
        assert "meeting_id" in (results[0].content or "")


class TestArtifactReaderQueryAll:
    """Verify ArtifactReader.query_all_artifacts."""

    def test_query_all(self) -> None:
        mock = MockReadDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()
        reader = ArtifactReader(_make_config())

        all_results = reader.query_all_artifacts(
            token=token,
            meeting_id="meeting_20260610_abc123",
        )

        assert "transcript" in all_results
        assert "context_packet" in all_results
        assert "decision" in all_results
        assert "manifest" in all_results
        assert len(all_results["transcript"]) == 3
        assert len(all_results["context_packet"]) == 2
        assert len(all_results["decision"]) == 2
        assert len(all_results["manifest"]) == 1

    def test_query_all_with_round_filter(self) -> None:
        mock = MockReadDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()
        reader = ArtifactReader(_make_config())

        all_results = reader.query_all_artifacts(
            token=token,
            meeting_id="meeting_20260610_abc123",
            round_number=1,
        )

        assert len(all_results["transcript"]) == 2  # round 1 + round 1 ceo
        assert len(all_results["context_packet"]) == 1  # round 1 role ceo
        # decisions and manifest are unaffected by round filter
        assert len(all_results["decision"]) == 2
        assert len(all_results["manifest"]) == 1


class TestArtifactReaderCache:
    """Verify folder cache behaviour."""

    def test_clear_cache(self) -> None:
        reader = ArtifactReader(_make_config())
        reader._folder_cache["key"] = "value"
        assert len(reader._folder_cache) == 1

        reader.clear_folder_cache()
        assert len(reader._folder_cache) == 0


# ═════════════════════════════════════════════════════════════════════════
# Build helper tests
# ═════════════════════════════════════════════════════════════════════════


class TestBuildReader:
    """Verify build_reader convenience function."""

    def test_default_build(self) -> None:
        reader = build_reader()
        assert reader.config.root_folder_name == DEFAULT_ROOT_FOLDER_NAME
        assert reader.config.request_timeout == 30.0

    def test_custom_build(self) -> None:
        reader = build_reader(
            root_folder_name="Custom_Read",
            request_timeout=20.0,
        )
        assert reader.config.root_folder_name == "Custom_Read"
        assert reader.config.request_timeout == 20.0


# ═════════════════════════════════════════════════════════════════════════
# Integration: writer + reader round-trip
# ═════════════════════════════════════════════════════════════════════════


class TestWriteThenRead:
    """Verify that artifacts written via the same mock can be read back.

    This simulates the full lifecycle: write artifacts to Drive via the
    writer's write functions, then read them back via the reader.
    """

    def test_write_then_read_transcript(self) -> None:
        mock = MockReadDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()

        # Use the writer to create a new transcript
        from src.gdrive_artifact_writer import (
            GDriveArtifactWriter,
            GDriveWriterConfig,
        )
        writer = GDriveArtifactWriter(
            GDriveWriterConfig(root_folder_name=DEFAULT_ROOT_FOLDER_NAME)
        )
        write_result = writer.write_transcript(
            token=token,
            meeting_id="meeting_20260610_abc123",
            content="# New Round 3\n\nQ3 planning completed.",
            round_number=3,
            role_id="coo",
        )
        assert write_result.success is True

        # Now read it back via the reader
        reader = ArtifactReader(
            ReaderConfig(root_folder_name=DEFAULT_ROOT_FOLDER_NAME)
        )
        infos = reader.query_artifacts(
            token=token,
            meeting_id="meeting_20260610_abc123",
            artifact_type="transcript",
            round_number=3,
            role_id="coo",
        )

        assert len(infos) == 1
        assert infos[0].round_number == 3
        assert infos[0].role_id == "coo"

        result = reader.download_artifact(token=token, file_id=infos[0].file_id)
        assert result.success is True
        content_stored = result.content
        assert content_stored is not None
        assert "Q3 planning" in content_stored

    def test_write_then_read_decision(self) -> None:
        mock = MockReadDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()

        from src.gdrive_artifact_writer import (
            GDriveArtifactWriter,
            GDriveWriterConfig,
        )
        writer = GDriveArtifactWriter(
            GDriveWriterConfig(root_folder_name=DEFAULT_ROOT_FOLDER_NAME)
        )
        write_result = writer.write_decision(
            token=token,
            meeting_id="meeting_20260610_abc123",
            content={"decision": "expand", "budget": 50000},
            decision_index=3,
        )
        assert write_result.success is True

        reader = ArtifactReader(
            ReaderConfig(root_folder_name=DEFAULT_ROOT_FOLDER_NAME)
        )
        results = reader.query_and_download(
            token=token,
            meeting_id="meeting_20260610_abc123",
            artifact_type="decision",
        )

        # Should find 3 decisions now (2 seeded + 1 new)
        assert len(results) >= 3
        d3 = [r for r in results if r.info and r.info.decision_index == 3]
        assert len(d3) == 1
        assert d3[0].content is not None
        parsed = json.loads(d3[0].content)
        assert parsed["decision"] == "expand"


# ═════════════════════════════════════════════════════════════════════════
# Error path tests
# ═════════════════════════════════════════════════════════════════════════


class TestErrorPaths:
    """Verify error handling in reader functions."""

    def test_list_files_auth_error(self) -> None:
        inject_drive_api(_mock_drive_auth_error)
        token = _make_token()

        with pytest.raises(ValueError):
            _list_files_in_folder(token=token, folder_id="any_folder")

    def test_list_files_server_error(self) -> None:
        inject_drive_api(_mock_drive_server_error)
        token = _make_token()

        with pytest.raises(ValueError):
            _list_files_in_folder(token=token, folder_id="any_folder")

    def test_find_artifacts_no_hierarchy(self) -> None:
        mock = MockReadDriveAPI(seed_hierarchy=False)
        inject_drive_api(mock.handle)
        token = _make_token()

        results = find_artifacts(
            token=token,
            meeting_id="meeting_abc",
            artifact_type="transcript",
        )

        assert results == []


# ═════════════════════════════════════════════════════════════════════════
# Edge case tests
# ═════════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    """Verify edge case behaviour."""

    def test_empty_content_download(self) -> None:
        """Downloading a file with empty content returns empty string."""
        mock = MockReadDriveAPI(seed_hierarchy=False)
        inject_drive_api(mock.handle)
        token = _make_token()

        # Create a folder hierarchy with an empty file
        root = mock._add_entry({
            "name": DEFAULT_ROOT_FOLDER_NAME,
            "mimeType": FOLDER_MIME_TYPE,
            "parents": [],
            "trashed": False,
        })
        meeting = mock._add_entry({
            "name": "meeting_empty",
            "mimeType": FOLDER_MIME_TYPE,
            "parents": [root["id"]],
            "trashed": False,
        })
        transcripts = mock._add_entry({
            "name": "transcripts",
            "mimeType": FOLDER_MIME_TYPE,
            "parents": [meeting["id"]],
            "trashed": False,
        })
        empty_file = mock._add_file_with_content({
            "name": "round_1_transcript.md",
            "mimeType": "text/markdown",
            "parents": [transcripts["id"]],
            "webViewLink": "",
            "trashed": False,
        }, "")

        infos = find_artifacts(
            token=token,
            meeting_id="meeting_empty",
            artifact_type="transcript",
        )

        assert len(infos) == 1
        result = read_artifact_content(token=token, file_id=infos[0].file_id)
        assert result.success is True
        assert result.content == ""

    def test_unicode_content(self) -> None:
        """Verify round-trip of Unicode content."""
        mock = MockReadDriveAPI(seed_hierarchy=False)
        inject_drive_api(mock.handle)
        token = _make_token()

        # Create hierarchy
        root = mock._add_entry({
            "name": DEFAULT_ROOT_FOLDER_NAME,
            "mimeType": FOLDER_MIME_TYPE,
            "parents": [],
            "trashed": False,
        })
        meeting = mock._add_entry({
            "name": "meeting_unicode",
            "mimeType": FOLDER_MIME_TYPE,
            "parents": [root["id"]],
            "trashed": False,
        })
        transcripts = mock._add_entry({
            "name": "transcripts",
            "mimeType": FOLDER_MIME_TYPE,
            "parents": [meeting["id"]],
            "trashed": False,
        })
        content = "한국어 회의록\n日本語の議事録\n🎉 Emoji test"
        mock._add_file_with_content({
            "name": "round_1_transcript.md",
            "mimeType": "text/markdown",
            "parents": [transcripts["id"]],
            "trashed": False,
        }, content)

        infos = find_artifacts(
            token=token,
            meeting_id="meeting_unicode",
            artifact_type="transcript",
        )

        result = read_artifact_content(token=token, file_id=infos[0].file_id)
        assert result.success is True
        assert result.content == content

    def test_role_id_case_insensitive_filter(self) -> None:
        mock = MockReadDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()

        results_lower = find_artifacts(
            token=token,
            meeting_id="meeting_20260610_abc123",
            artifact_type="transcript",
            role_id="ceo",
        )
        results_upper = find_artifacts(
            token=token,
            meeting_id="meeting_20260610_abc123",
            artifact_type="transcript",
            role_id="CEO",
        )

        assert len(results_lower) == len(results_upper)
        assert len(results_lower) >= 1


# ═════════════════════════════════════════════════════════════════════════
# ArtifactType re-export tests
# ═════════════════════════════════════════════════════════════════════════


class TestArtifactTypeReExport:
    """Verify ArtifactType is accessible from the reader module."""

    def test_re_exported_from_writer(self) -> None:
        from src.gdrive_artifact_reader import ArtifactType as RArtifactType
        from src.gdrive_artifact_writer import ArtifactType as WArtifactType
        assert RArtifactType is WArtifactType
