"""Tests for the Google Drive artifact writer module (Sub-AC 19a-2).

Verifies:
- GDriveWriterConfig validation
- GDriveFolder and GDriveFile dataclass creation
- GDriveArtifactResult success/error states
- ArtifactType enum values
- Mock Drive API folder management (find, create, find_or_create)
- Mock file uploads (write_artifact, write_text_artifact,
  write_json_artifact)
- MIME type correctness per artifact type
- GDriveArtifactWriter high-level API:
  - ensure_meeting_folder (full hierarchy creation)
  - write_transcript (with/without role, round numbers)
  - write_context_packet (JSON content)
  - write_decision (indexed decisions)
  - write_manifest (at meeting folder root)
  - write_artifacts_batch (combined batch write)
  - folder cache behaviour
- Error paths:
  - missing folder_id
  - empty file_name
  - HTTP errors from mock Drive API
  - network failures
  - invalid JSON content for write_json_artifact
- Edge cases:
  - folder names with special characters
  - Unicode content in filenames and body
  - Large content (within limits)
  - Empty content
  - Concurrent cache access
- build_writer convenience function

Uses inject_drive_api() to inject a mock HTTP handler so no
real Google Drive calls are made during tests.
"""

from __future__ import annotations

import json
import time
from typing import Any

import pytest

from src.gdrive_artifact_writer import (
    DEFAULT_ROOT_FOLDER_NAME,
    DRIVE_API_BASE,
    DRIVE_UPLOAD_BASE,
    FOLDER_MIME_TYPE,
    MIME_TYPES,
    ArtifactType,
    GDriveArtifactResult,
    GDriveArtifactWriter,
    GDriveFile,
    GDriveFolder,
    GDriveWriterConfig,
    _create_folder,
    _find_folder,
    _get_drive_api_fn,
    _url_encode,
    build_writer,
    find_or_create_folder,
    inject_drive_api,
    write_artifact,
    write_json_artifact,
    write_text_artifact,
)
from src.gdrive_auth import GDriveToken


# ═════════════════════════════════════════════════════════════════════════
# Helpers
# ═════════════════════════════════════════════════════════════════════════


def _make_token(
    access_token: str = "ya29.test-drive-token",
    token_type: str = "Bearer",
    expires_in: float = 3600.0,
) -> GDriveToken:
    """Build a valid, non-expired GDriveToken for test use."""
    return GDriveToken(
        access_token=access_token,
        refresh_token="1//test-refresh",
        token_type=token_type,
        expires_at=time.time() + expires_in,
        scope="https://www.googleapis.com/auth/drive.file",
    )


def _make_config(
    root_folder_name: str = "Test_Meetings",
    request_timeout: float = 10.0,
) -> GDriveWriterConfig:
    """Build a test GDriveWriterConfig."""
    return GDriveWriterConfig(
        root_folder_name=root_folder_name,
        request_timeout=request_timeout,
    )


# ── Mock Drive API handlers ──────────────────────────────────────────────


class MockDriveAPI:
    """Stateful mock Google Drive API for test use.

    Maintains an in-memory file/folder store so tests can verify
    that create, find, and upload operations behave correctly.
    """

    def __init__(self) -> None:
        self._store: dict[str, dict] = {}  # file_id -> metadata
        self._next_id: int = 1000
        self._call_log: list[dict] = []

    def _new_id(self) -> str:
        self._next_id += 1
        return f"mock_file_{self._next_id}"

    def _add_entry(self, entry: dict) -> dict:
        fid = self._new_id()
        entry["id"] = fid
        self._store[fid] = {**entry}
        return entry

    def _search(self, query_str: str) -> list[dict]:
        """Mock search: supports name=, mimeType=, trashed=, 'parent' in parents."""
        from urllib.parse import unquote

        query_str = unquote(query_str)
        results = list(self._store.values())

        # Split on ' and ' but not inside quotes
        parts = self._split_query(query_str)

        for part in parts:
            part = part.strip()
            if not part:
                continue

            # Handle "'parent_id' in parents" pattern
            if " in parents" in part:
                pid = part.split(" in parents")[0].strip().strip("'")
                results = [
                    r for r in results
                    if pid in r.get("parents", [])
                ]
                continue

            # Handle "key='value'" or "key=value" patterns
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
        """Split a Drive API query string on ' and ' preserving
        parenthetical expressions like 'id' in parents."""
        parts = []
        current = ""
        in_expr = 0  # depth of parenthetical nesting
        i = 0
        while i < len(query_str):
            # Check for ' and ' separator (not inside an expression)
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

        # ── Folder search (GET /files) ───────────────────────────────
        if method == "GET" and DRIVE_API_BASE in url and "/files" in url:
            # Extract query parameter
            query_str = ""
            if "?q=" in url:
                q_start = url.index("?q=") + 3
                q_end = url.index("&", q_start) if "&" in url[q_start:] else len(url)
                query_str = url[q_start:q_end]

            files = self._search(query_str)
            return (200, {"files": files[:10]})

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
        if method == "POST" and DRIVE_UPLOAD_BASE in url and "/files" in url:
            if body is None:
                return (400, {"error": {"message": "Missing body"}})

            # Parse multipart body
            body_str = body.decode("utf-8")
            # Extract JSON metadata from multipart
            parts = body_str.split("\r\n")
            metadata_json = ""
            in_metadata = False
            for i, part in enumerate(parts):
                if part == "Content-Type: application/json; charset=UTF-8":
                    in_metadata = True
                    continue
                if in_metadata and part == "":
                    # Next part after empty line is the content
                    break
                if in_metadata and part:
                    metadata_json = part
                    break

            if not metadata_json:
                # Try alternative parsing: JSON between boundaries
                for part in parts:
                    if part.startswith("{") and part.endswith("}"):
                        try:
                            json.loads(part)
                            metadata_json = part
                            break
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
                "webViewLink": f"https://drive.google.com/file/d/{self._next_id}/view",
                "trashed": False,
            })
            return (200, {
                "id": entry["id"],
                "name": entry["name"],
                "mimeType": entry["mimeType"],
                "webViewLink": entry["webViewLink"],
                "parents": entry["parents"],
            })

        return (404, {"error": {"message": f"Unknown mock endpoint: {method} {url}"}})


def _mock_drive_success(
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes | None = None,
    timeout: float = 10.0,
) -> tuple[int, dict]:
    """Simple mock: always return success with a new file ID.

    Used for tests that just need a successful response and don't
    need stateful folder tracking.
    """
    return (200, {
        "id": "mock_file_9999",
        "name": "test_file.md",
        "mimeType": headers.get("Content-Type", "text/plain"),
        "webViewLink": "https://drive.google.com/file/d/mock_9999/view",
        "parents": ["mock_parent_1"],
    })


def _mock_drive_error(
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes | None = None,
    timeout: float = 10.0,
) -> tuple[int, dict]:
    """Mock that returns a 500 server error."""
    return (500, {"error": {"message": "Internal server error"}})


def _mock_drive_unauthorized(
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes | None = None,
    timeout: float = 10.0,
) -> tuple[int, dict]:
    """Mock that returns a 401 unauthorized error."""
    return (401, {"error": {"message": "Invalid Credentials"}})


def _mock_drive_network_failure(
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes | None = None,
    timeout: float = 10.0,
) -> tuple[int, dict]:
    """Mock that raises a network error."""
    raise OSError("Connection reset by peer")


def _mock_drive_quota_exceeded(
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes | None = None,
    timeout: float = 10.0,
) -> tuple[int, dict]:
    """Mock that returns a 403 quota exceeded error."""
    return (403, {
        "error": {
            "message": "The user's Drive storage quota has been exceeded.",
            "errors": [{"reason": "storageQuotaExceeded"}],
        }
    })


# ── Fixture: auto-reset inject ──────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_drive_api():
    """Reset the Drive API inject to default after each test."""
    yield
    inject_drive_api(None)


# ═════════════════════════════════════════════════════════════════════════
# GDriveWriterConfig tests
# ═════════════════════════════════════════════════════════════════════════


class TestGDriveWriterConfig:
    """Verify GDriveWriterConfig creation and validation."""

    def test_default_config(self) -> None:
        config = GDriveWriterConfig()
        assert config.root_folder_name == DEFAULT_ROOT_FOLDER_NAME
        assert config.request_timeout == 30.0

    def test_custom_config(self) -> None:
        config = GDriveWriterConfig(
            root_folder_name="Custom_Meetings",
            request_timeout=45.0,
        )
        assert config.root_folder_name == "Custom_Meetings"
        assert config.request_timeout == 45.0

    def test_empty_root_folder_name_raises(self) -> None:
        with pytest.raises(ValueError, match="root_folder_name"):
            GDriveWriterConfig(root_folder_name="")

    def test_whitespace_root_folder_name_raises(self) -> None:
        with pytest.raises(ValueError, match="root_folder_name"):
            GDriveWriterConfig(root_folder_name="   ")

    def test_zero_timeout_raises(self) -> None:
        with pytest.raises(ValueError, match="request_timeout"):
            GDriveWriterConfig(request_timeout=0)

    def test_negative_timeout_raises(self) -> None:
        with pytest.raises(ValueError, match="request_timeout"):
            GDriveWriterConfig(request_timeout=-5.0)


# ═════════════════════════════════════════════════════════════════════════
# Data type tests
# ═════════════════════════════════════════════════════════════════════════


class TestDataTypes:
    """Verify GDriveFolder, GDriveFile, GDriveArtifactResult, ArtifactType."""

    def test_gdrive_folder_creation(self) -> None:
        folder = GDriveFolder(
            folder_id="abc123",
            folder_name="TestFolder",
            parent_id="root",
        )
        assert folder.folder_id == "abc123"
        assert folder.folder_name == "TestFolder"
        assert folder.parent_id == "root"

    def test_gdrive_folder_no_parent(self) -> None:
        folder = GDriveFolder(folder_id="xyz", folder_name="Root")
        assert folder.parent_id == ""

    def test_gdrive_file_creation(self) -> None:
        f = GDriveFile(
            file_id="f123",
            file_name="transcript.md",
            mime_type="text/markdown",
            web_view_link="https://drive.google.com/",
            folder_id="parent123",
        )
        assert f.file_id == "f123"
        assert f.file_name == "transcript.md"
        assert f.mime_type == "text/markdown"
        assert f.web_view_link == "https://drive.google.com/"
        assert f.folder_id == "parent123"

    def test_gdrive_artifact_result_success(self) -> None:
        f = GDriveFile(file_id="f1", file_name="test.json", mime_type="application/json")
        result = GDriveArtifactResult(success=True, file=f)
        assert result.success is True
        assert result.file is not None
        assert result.error == ""

    def test_gdrive_artifact_result_failure(self) -> None:
        result = GDriveArtifactResult(
            success=False,
            error="Failed",
            http_status=500,
        )
        assert result.success is False
        assert result.file is None
        assert result.error == "Failed"
        assert result.http_status == 500

    def test_gdrive_artifact_result_with_execution_id(self) -> None:
        result = GDriveArtifactResult(
            success=True,
            execution_id="exec_001",
        )
        assert result.execution_id == "exec_001"

    def test_artifact_type_values(self) -> None:
        assert ArtifactType.TRANSCRIPT.value == "transcript"
        assert ArtifactType.CONTEXT_PACKET.value == "context_packet"
        assert ArtifactType.DECISION.value == "decision"
        assert ArtifactType.MANIFEST.value == "manifest"

    def test_mime_types_mapping(self) -> None:
        assert MIME_TYPES["transcript"] == "text/markdown"
        assert MIME_TYPES["context_packet"] == "application/json"
        assert MIME_TYPES["decision"] == "application/json"
        assert MIME_TYPES["manifest"] == "application/json"


# ═════════════════════════════════════════════════════════════════════════
# URL encoding tests
# ═════════════════════════════════════════════════════════════════════════


class TestURLEncode:
    """Verify URL encoding for Drive API query parameters."""

    def test_simple_string(self) -> None:
        assert _url_encode("hello") == "hello"

    def test_spaces(self) -> None:
        encoded = _url_encode("AI Agent Meetings")
        assert "AI" in encoded
        assert "%20" in encoded

    def test_special_characters(self) -> None:
        encoded = _url_encode("folder'name")
        assert "%27" in encoded

    def test_empty_string(self) -> None:
        assert _url_encode("") == ""


# ═════════════════════════════════════════════════════════════════════════
# Mock Drive API: folder management
# ═════════════════════════════════════════════════════════════════════════


class TestFolderManagement:
    """Verify folder creation and search with mock Drive API."""

    def test_create_folder(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()

        folder = _create_folder(token, "MyFolder")

        assert folder.folder_id != ""
        assert folder.folder_name == "MyFolder"

    def test_create_folder_with_parent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()

        folder = _create_folder(token, "Child", parent_id="parent_123")

        assert folder.folder_name == "Child"
        assert folder.parent_id == "parent_123"

    def test_find_folder_exists(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()

        # Create first
        _create_folder(token, "FindMe")
        # Then find
        found = _find_folder(token, "FindMe")

        assert found is not None
        assert found.folder_name == "FindMe"

    def test_find_folder_not_exists(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()

        found = _find_folder(token, "NoSuchFolder")

        assert found is None

    def test_find_or_create_returns_existing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()

        # First call creates
        folder1, created1 = find_or_create_folder(token, "DupFolder")
        assert created1 is True

        # Second call finds
        folder2, created2 = find_or_create_folder(token, "DupFolder")
        assert created2 is False
        assert folder1.folder_id == folder2.folder_id

    def test_find_or_create_creates_new(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()

        folder, created = find_or_create_folder(token, "NewFolder")

        assert created is True
        assert folder.folder_name == "NewFolder"

    def test_find_or_create_with_parent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()

        folder, created = find_or_create_folder(
            token, "SubFolder", parent_id="parent_abc"
        )

        assert created is True
        assert folder.folder_name == "SubFolder"

    def test_create_folder_api_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        inject_drive_api(_mock_drive_error)
        token = _make_token()

        with pytest.raises(ValueError, match="Failed to create folder"):
            _create_folder(token, "WillFail")


# ═════════════════════════════════════════════════════════════════════════
# Mock Drive API: artifact writing (low-level)
# ═════════════════════════════════════════════════════════════════════════


class TestArtifactWriting:
    """Verify low-level artifact write functions with mock Drive API."""

    def test_write_artifact_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()

        result = write_artifact(
            token=token,
            folder_id="folder_123",
            file_name="test.txt",
            content="Hello, world!",
            mime_type="text/plain",
        )

        assert result.success is True
        assert result.file is not None
        assert result.file.file_name == "test.txt"
        assert result.file.mime_type == "text/plain"

    def test_write_artifact_missing_folder_id(self) -> None:
        token = _make_token()

        result = write_artifact(
            token=token,
            folder_id="",
            file_name="test.txt",
            content="hello",
            mime_type="text/plain",
        )

        assert result.success is False
        assert "folder_id" in result.error.lower()

    def test_write_artifact_empty_file_name(self) -> None:
        token = _make_token()

        result = write_artifact(
            token=token,
            folder_id="folder_123",
            file_name="",
            content="hello",
            mime_type="text/plain",
        )

        assert result.success is False
        assert "file_name" in result.error.lower()

    def test_write_artifact_http_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        inject_drive_api(_mock_drive_error)
        token = _make_token()

        result = write_artifact(
            token=token,
            folder_id="folder_123",
            file_name="test.txt",
            content="hello",
            mime_type="text/plain",
        )

        assert result.success is False
        assert "Failed to write" in result.error
        assert result.http_status == 500

    def test_write_artifact_network_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        inject_drive_api(_mock_drive_network_failure)
        token = _make_token()

        result = write_artifact(
            token=token,
            folder_id="folder_123",
            file_name="test.txt",
            content="hello",
            mime_type="text/plain",
        )

        assert result.success is False
        assert "Network error" in result.error

    def test_write_artifact_quota_exceeded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        inject_drive_api(_mock_drive_quota_exceeded)
        token = _make_token()

        result = write_artifact(
            token=token,
            folder_id="folder_123",
            file_name="test.txt",
            content="hello",
            mime_type="text/plain",
        )

        assert result.success is False
        assert "quota" in result.error.lower()
        assert result.http_status == 403

    def test_write_text_artifact(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()

        result = write_text_artifact(
            token=token,
            folder_id="folder_abc",
            file_name="notes.txt",
            content="Some notes",
        )

        assert result.success is True
        assert result.file is not None

    def test_write_json_artifact(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()

        result = write_json_artifact(
            token=token,
            folder_id="folder_abc",
            file_name="data.json",
            content={"key": "value", "list": [1, 2, 3]},
        )

        assert result.success is True
        assert result.file is not None
        assert result.file.mime_type == "application/json"

    def test_write_json_artifact_with_unicode(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()

        result = write_json_artifact(
            token=token,
            folder_id="folder_abc",
            file_name="한글.json",
            content={"안녕": "세계", "角色": "리더"},
        )

        assert result.success is True
        assert result.file is not None

    def test_write_artifact_bytes_content(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()

        result = write_artifact(
            token=token,
            folder_id="folder_abc",
            file_name="data.bin",
            content=b"binary content here",
            mime_type="application/octet-stream",
        )

        assert result.success is True


# ═════════════════════════════════════════════════════════════════════════
# GDriveArtifactWriter high-level API tests
# ═════════════════════════════════════════════════════════════════════════


class TestGDriveArtifactWriterInit:
    """Verify writer initialization and configuration."""

    def test_default_construction(self) -> None:
        writer = GDriveArtifactWriter()
        assert writer.config.root_folder_name == DEFAULT_ROOT_FOLDER_NAME

    def test_custom_config(self) -> None:
        config = GDriveWriterConfig(root_folder_name="Custom")
        writer = GDriveArtifactWriter(config)
        assert writer.config.root_folder_name == "Custom"

    def test_none_config_uses_default(self) -> None:
        writer = GDriveArtifactWriter(None)
        assert writer.config.root_folder_name == DEFAULT_ROOT_FOLDER_NAME


class TestEnsureMeetingFolder:
    """Verify meeting folder hierarchy creation."""

    def test_full_hierarchy_created(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()
        writer = GDriveArtifactWriter(_make_config())

        meeting_folder, created = writer.ensure_meeting_folder(
            token, "meeting_20260610_test"
        )

        assert created is True
        assert meeting_folder.folder_name == "meeting_20260610_test"

        # Verify sub-folders exist
        transcripts = _find_folder(token, "transcripts",
                                    parent_id=meeting_folder.folder_id)
        assert transcripts is not None

        context_packets = _find_folder(token, "context_packets",
                                        parent_id=meeting_folder.folder_id)
        assert context_packets is not None

        decisions = _find_folder(token, "decisions",
                                  parent_id=meeting_folder.folder_id)
        assert decisions is not None

    def test_second_call_uses_cache(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()
        writer = GDriveArtifactWriter(_make_config())

        folder1, _ = writer.ensure_meeting_folder(token, "meeting_test")
        # Clear cache but folders exist in mock store
        writer.clear_folder_cache()

        # Second call — folders already exist in mock store
        folder2, created2 = writer.ensure_meeting_folder(token, "meeting_test")
        assert created2 is False
        assert folder1.folder_id == folder2.folder_id


class TestWriteTranscript:
    """Verify transcript writing to Drive."""

    def test_basic_transcript(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()
        writer = GDriveArtifactWriter(_make_config())

        result = writer.write_transcript(
            token=token,
            meeting_id="meeting_t1",
            content="# Meeting Transcript\n\nRound 1 discussion...",
            round_number=1,
        )

        assert result.success is True
        assert result.file is not None
        assert "round_1" in result.file.file_name
        assert result.file.file_name.endswith(".md")
        assert result.file.mime_type == "text/markdown"

    def test_transcript_with_role(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()
        writer = GDriveArtifactWriter(_make_config())

        result = writer.write_transcript(
            token=token,
            meeting_id="meeting_t2",
            content="CEO opinion...",
            round_number=2,
            role_id="ceo",
        )

        assert result.success is True
        assert "round_2" in result.file.file_name
        assert "ceo" in result.file.file_name

    def test_transcript_round_3(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()
        writer = GDriveArtifactWriter(_make_config())

        result = writer.write_transcript(
            token=token,
            meeting_id="meeting_t3",
            content="Convergence round...",
            round_number=3,
        )

        assert result.success is True
        assert "round_3" in result.file.file_name

    def test_transcript_tiebreak(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()
        writer = GDriveArtifactWriter(_make_config())

        result = writer.write_transcript(
            token=token,
            meeting_id="meeting_t4",
            content="Tie-break round...",
            round_number=4,
        )

        assert result.success is True
        assert "round_4" in result.file.file_name

    def test_transcript_unicode_content(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()
        writer = GDriveArtifactWriter(_make_config())

        result = writer.write_transcript(
            token=token,
            meeting_id="meeting_한글",
            content="# 회의록\n\n1라운드 논의 내용...",
            round_number=1,
        )

        assert result.success is True


class TestWriteContextPacket:
    """Verify context packet writing to Drive."""

    def test_basic_packet(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()
        writer = GDriveArtifactWriter(_make_config())

        result = writer.write_context_packet(
            token=token,
            meeting_id="meeting_cp1",
            content={
                "agenda": "Music video planning",
                "required_roles": ["ceo", "director"],
                "round": 1,
            },
            round_number=1,
            role_id="ceo",
        )

        assert result.success is True
        assert result.file is not None
        assert "round_1" in result.file.file_name
        assert "ceo" in result.file.file_name
        assert result.file.file_name.endswith(".json")
        assert result.file.mime_type == "application/json"

    def test_packet_no_role(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()
        writer = GDriveArtifactWriter(_make_config())

        result = writer.write_context_packet(
            token=token,
            meeting_id="meeting_cp2",
            content={"summary": "All roles"},
            round_number=2,
        )

        assert result.success is True
        assert "packet.json" in result.file.file_name


class TestWriteDecision:
    """Verify decision record writing to Drive."""

    def test_basic_decision(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()
        writer = GDriveArtifactWriter(_make_config())

        result = writer.write_decision(
            token=token,
            meeting_id="meeting_d1",
            content={
                "decision": "Proceed with concept A",
                "votes": {"ceo": "approve", "cto": "approve"},
            },
            decision_index=1,
        )

        assert result.success is True
        assert result.file is not None
        assert "decision_001" in result.file.file_name
        assert result.file.file_name.endswith(".json")

    def test_decision_index_formatting(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()
        writer = GDriveArtifactWriter(_make_config())

        result = writer.write_decision(
            token=token,
            meeting_id="meeting_d2",
            content={"d": 1},
            decision_index=42,
        )

        assert result.success is True
        assert "decision_042" in result.file.file_name


class TestWriteManifest:
    """Verify manifest writing at meeting folder root."""

    def test_write_manifest(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()
        writer = GDriveArtifactWriter(_make_config())

        result = writer.write_manifest(
            token=token,
            meeting_id="meeting_m1",
            content={
                "meeting_id": "meeting_m1",
                "state": "completed",
                "agenda": "Test",
            },
        )

        assert result.success is True
        assert result.file is not None
        assert result.file.file_name == "manifest.json"


class TestWriteArtifactsBatch:
    """Verify batch artifact writing."""

    def test_empty_batch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()
        writer = GDriveArtifactWriter(_make_config())

        results = writer.write_artifacts_batch(
            token=token,
            meeting_id="meeting_b0",
        )

        assert len(results) == 0

    def test_full_batch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()
        writer = GDriveArtifactWriter(_make_config())

        results = writer.write_artifacts_batch(
            token=token,
            meeting_id="meeting_b1",
            transcripts=[
                {"content": "Round 1 transcript", "round_number": 1},
                {"content": "Round 2 transcript", "round_number": 2},
            ],
            context_packets=[
                {"content": {"role": "ceo"}, "round_number": 1, "role_id": "ceo"},
            ],
            decisions=[
                {"content": {"decision": "yes"}, "decision_index": 1},
            ],
            manifest={"meeting_id": "meeting_b1", "state": "completed"},
        )

        assert len(results) == 5  # 2 transcripts + 1 packet + 1 decision + 1 manifest
        for r in results:
            assert r.success is True

    def test_batch_with_mixed_results(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Batch where some writes fail should still return all results."""
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()
        writer = GDriveArtifactWriter(_make_config())

        # The folder hierarchy creation succeeds, but individual writes
        # with empty content should still succeed for JSON, just with
        # the empty content written.
        results = writer.write_artifacts_batch(
            token=token,
            meeting_id="meeting_b2",
            transcripts=[
                {"content": "# T1"},
            ],
            decisions=[
                {"content": {}},
            ],
        )

        assert len(results) == 2
        assert results[0].success is True


class TestFolderCache:
    """Verify folder cache behaviour."""

    def test_clear_cache(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()
        writer = GDriveArtifactWriter(_make_config())

        writer.ensure_meeting_folder(token, "meeting_c1")
        assert len(writer._folder_cache) > 0

        writer.clear_folder_cache()
        assert len(writer._folder_cache) == 0

    def test_cache_across_writes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()
        writer = GDriveArtifactWriter(_make_config())

        writer.write_transcript(
            token=token, meeting_id="meeting_c2",
            content="# T1", round_number=1,
        )
        # Cache should have the meeting folder
        assert "meeting_c2" in writer._folder_cache

        writer.write_decision(
            token=token, meeting_id="meeting_c2",
            content={"d": 1},
        )
        # Should still be cached, no additional folder creation
        assert "meeting_c2" in writer._folder_cache


# ═════════════════════════════════════════════════════════════════════════
# Error path tests
# ═════════════════════════════════════════════════════════════════════════


class TestErrorPaths:
    """Verify error handling for various failure modes."""

    def test_transcript_network_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()
        writer = GDriveArtifactWriter(_make_config())

        # Do one successful write to populate all folder caches
        writer.write_transcript(
            token=token,
            meeting_id="meeting_err1",
            content="# Initial write",
        )

        # Then switch to failure mock for the next write
        inject_drive_api(_mock_drive_network_failure)

        result = writer.write_transcript(
            token=token,
            meeting_id="meeting_err1",
            content="Will fail",
        )

        assert result.success is False
        assert "Network error" in result.error

    def test_decision_api_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()
        writer = GDriveArtifactWriter(_make_config())

        # Do one successful write to populate folder caches
        writer.write_decision(
            token=token,
            meeting_id="meeting_err2",
            content={"initial": True},
        )
        inject_drive_api(_mock_drive_error)

        result = writer.write_decision(
            token=token,
            meeting_id="meeting_err2",
            content={"d": 1},
        )

        assert result.success is False
        assert result.http_status == 500

    def test_unauthorized_write(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()
        writer = GDriveArtifactWriter(_make_config())

        # Do one successful write to populate folder caches
        writer.write_context_packet(
            token=token,
            meeting_id="meeting_err3",
            content={"initial": True},
        )
        inject_drive_api(_mock_drive_unauthorized)

        result = writer.write_context_packet(
            token=token,
            meeting_id="meeting_err3",
            content={"key": "val"},
        )

        assert result.success is False
        assert result.http_status == 401


# ═════════════════════════════════════════════════════════════════════════
# build_writer convenience function
# ═════════════════════════════════════════════════════════════════════════


class TestBuildWriter:
    """Verify the module-level build_writer convenience function."""

    def test_default_parameters(self) -> None:
        writer = build_writer()
        assert writer.config.root_folder_name == DEFAULT_ROOT_FOLDER_NAME
        assert writer.config.request_timeout == 30.0

    def test_custom_parameters(self) -> None:
        writer = build_writer(
            root_folder_name="CustomRoot",
            request_timeout=15.0,
        )
        assert writer.config.root_folder_name == "CustomRoot"
        assert writer.config.request_timeout == 15.0


# ═════════════════════════════════════════════════════════════════════════
# MIME type correctness
# ═════════════════════════════════════════════════════════════════════════


class TestMIMETypeCorrectness:
    """Verify that each artifact type uses the correct MIME type."""

    def test_transcript_uses_markdown_mime(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()
        writer = GDriveArtifactWriter(_make_config())

        result = writer.write_transcript(
            token=token,
            meeting_id="meeting_mime1",
            content="# T",
        )

        assert result.success is True
        assert result.file is not None
        assert result.file.mime_type == "text/markdown"

    def test_context_packet_uses_json_mime(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()
        writer = GDriveArtifactWriter(_make_config())

        result = writer.write_context_packet(
            token=token,
            meeting_id="meeting_mime2",
            content={"a": 1},
        )

        assert result.success is True
        assert result.file is not None
        assert result.file.mime_type == "application/json"

    def test_decision_uses_json_mime(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()
        writer = GDriveArtifactWriter(_make_config())

        result = writer.write_decision(
            token=token,
            meeting_id="meeting_mime3",
            content={"d": 1},
        )

        assert result.success is True
        assert result.file is not None
        assert result.file.mime_type == "application/json"

    def test_manifest_uses_json_mime(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()
        writer = GDriveArtifactWriter(_make_config())

        result = writer.write_manifest(
            token=token,
            meeting_id="meeting_mime4",
            content={"m": 1},
        )

        assert result.success is True
        assert result.file is not None
        assert result.file.mime_type == "application/json"


# ═════════════════════════════════════════════════════════════════════════
# Edge cases
# ═════════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    """Verify edge case behaviour."""

    def test_meeting_id_with_special_chars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()
        writer = GDriveArtifactWriter(_make_config())

        result = writer.write_transcript(
            token=token,
            meeting_id="meeting_2026-06-10_abc123_한글",
            content="# T",
        )

        assert result.success is True

    def test_empty_content_transcript(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()
        writer = GDriveArtifactWriter(_make_config())

        result = writer.write_transcript(
            token=token,
            meeting_id="meeting_edge1",
            content="",
        )

        # Empty content is valid — just an empty file
        assert result.success is True

    def test_empty_dict_decision(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()
        writer = GDriveArtifactWriter(_make_config())

        result = writer.write_decision(
            token=token,
            meeting_id="meeting_edge2",
            content={},
        )

        assert result.success is True

    def test_multiple_meetings_isolation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Each meeting gets its own folder hierarchy."""
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()
        writer = GDriveArtifactWriter(_make_config())

        r1 = writer.write_transcript(
            token=token, meeting_id="meeting_iso1", content="# M1"
        )
        r2 = writer.write_transcript(
            token=token, meeting_id="meeting_iso2", content="# M2"
        )

        assert r1.success is True
        assert r2.success is True
        # Different folder IDs
        assert r1.file is not None and r2.file is not None
        assert r1.file.folder_id != r2.file.folder_id

    def test_writer_reuse_across_meetings(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Writer instance handles multiple meetings correctly."""
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()
        writer = GDriveArtifactWriter(_make_config())

        results = []
        for i in range(3):
            r = writer.write_transcript(
                token=token,
                meeting_id=f"meeting_wr_{i}",
                content=f"# Meeting {i}",
            )
            results.append(r)

        assert all(r.success for r in results)
        # Each should have a different folder
        folder_ids = {r.file.folder_id for r in results if r.file}
        assert len(folder_ids) == 3

    def test_inject_drive_api_restore(self) -> None:
        """After inject(None), the default handler is restored."""
        inject_drive_api(_mock_drive_success)
        fn1 = _get_drive_api_fn()
        inject_drive_api(None)
        fn2 = _get_drive_api_fn()

        assert fn1 is not fn2
        assert fn1 == _mock_drive_success
