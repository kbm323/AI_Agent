"""Tests for the meeting folder structure manager (Sub-AC 19a-4).

Verifies:
- FolderManagerConfig validation
- FolderLayer enum values
- MeetingFolderStructure creation and properties
- KnowledgeFolderStructure creation and layer mapping
- AgentFolderStructure creation
- FullSystemStructure creation
- FolderCreationResult layer tracking
- MeetingFolderManager core behaviours:
  - ensure_root_folder (create and idempotent re-call)
  - ensure_system_containers (create and idempotent)
  - ensure_meeting_hierarchy (full tree, idempotent, isolation)
  - ensure_knowledge_structure (L0-L3 layers)
  - ensure_agent_structure (persona + model configs)
  - ensure_full_system (all in one)
  - list_meeting_sub_folders (existing and non-existing)
  - verify_hierarchy_exists
  - cache management (clear_cache, cache_size)
- Idempotency: repeated calls with same meeting_id must
  return existing folders (folders_already_existed=True)
- Meeting isolation: different meeting_ids get different
  folder trees
- Error paths:
  - empty meeting_id
  - empty root_folder_name in config
  - zero/negative timeout
  - API errors during folder creation
- Edge cases:
  - Unicode meeting IDs
  - Special characters in folder names
  - Multiple concurrent meetings with isolation
  - Large number of repeated calls (idempotency stress)
- build_folder_manager convenience function

Uses inject_drive_api() to inject a mock HTTP handler so no
real Google Drive calls are made during tests.
"""

from __future__ import annotations

import json
import time
from typing import Any, Generator

import pytest

from src.gdrive_artifact_writer import (
    DEFAULT_ROOT_FOLDER_NAME,
    DEFAULT_REQUEST_TIMEOUT,
    DRIVE_API_BASE,
    FOLDER_MIME_TYPE,
    GDriveFolder,
    _find_folder,
    find_or_create_folder,
    inject_drive_api,
)
from src.gdrive_auth import GDriveToken
from src.meeting_folder_manager import (
    AGENT_SUB_FOLDERS,
    KNOWLEDGE_LAYER_FOLDERS,
    MEETING_SUB_FOLDERS,
    SYSTEM_FOLDERS,
    AgentFolderStructure,
    FolderCreationResult,
    FolderLayer,
    FolderManagerConfig,
    FullSystemStructure,
    KnowledgeFolderStructure,
    MeetingFolderManager,
    MeetingFolderStructure,
    build_folder_manager,
)

# ═════════════════════════════════════════════════════════════════════════
# Helpers
# ═════════════════════════════════════════════════════════════════════════


def _make_token(
    access_token: str = "ya29.test-folder-mgr-token",
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
) -> FolderManagerConfig:
    """Build a test FolderManagerConfig."""
    return FolderManagerConfig(
        root_folder_name=root_folder_name,
        request_timeout=request_timeout,
    )


# ═════════════════════════════════════════════════════════════════════════
# Stateful mock Drive API
# ═════════════════════════════════════════════════════════════════════════


class MockDriveAPI:
    """Stateful mock Google Drive API for folder manager tests.

    Maintains an in-memory file/folder store so tests can verify
    that create, find, and idempotent operations behave correctly.
    Mimics the real Drive API v3 behaviour for folder operations.
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
        """Mock search: supports name=, mimeType=, trashed=, parent in parents."""
        from urllib.parse import unquote

        query_str = unquote(query_str)
        results = list(self._store.values())

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

        # ── Folder search (GET /files) ───────────────────────────────
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

        return (404, {"error": {"message": f"Unknown mock endpoint: {method} {url}"}})

    @property
    def total_entries(self) -> int:
        """Return the number of entries in the mock store."""
        return len(self._store)

    @property
    def folder_count(self) -> int:
        """Return the number of folder entries in the mock store."""
        return sum(
            1 for e in self._store.values()
            if e.get("mimeType") == FOLDER_MIME_TYPE
        )


# ═════════════════════════════════════════════════════════════════════════
# Error mocks
# ═════════════════════════════════════════════════════════════════════════


def _mock_drive_server_error(
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes | None = None,
    timeout: float = 10.0,
) -> tuple[int, dict]:
    """Mock that returns a 500 server error."""
    return (500, {"error": {"message": "Internal server error"}})


def _mock_drive_network_failure(
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes | None = None,
    timeout: float = 10.0,
) -> tuple[int, dict]:
    """Mock that raises a network error."""
    raise OSError("Connection reset by peer")


# ── Fixture: auto-reset inject ──────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_drive_api() -> Generator[None, None, None]:
    """Reset the Drive API inject to default after each test."""
    yield
    inject_drive_api(None)


# ═════════════════════════════════════════════════════════════════════════
# FolderManagerConfig tests
# ═════════════════════════════════════════════════════════════════════════


class TestFolderManagerConfig:
    """Verify FolderManagerConfig creation and validation."""

    def test_default_config(self) -> None:
        config = FolderManagerConfig()
        assert config.root_folder_name == DEFAULT_ROOT_FOLDER_NAME
        assert config.request_timeout == DEFAULT_REQUEST_TIMEOUT

    def test_custom_config(self) -> None:
        config = FolderManagerConfig(
            root_folder_name="CustomMeetings",
            request_timeout=15.0,
        )
        assert config.root_folder_name == "CustomMeetings"
        assert config.request_timeout == 15.0

    def test_empty_root_folder_name_raises(self) -> None:
        with pytest.raises(ValueError, match="root_folder_name"):
            FolderManagerConfig(root_folder_name="")

    def test_whitespace_root_folder_name_raises(self) -> None:
        with pytest.raises(ValueError, match="root_folder_name"):
            FolderManagerConfig(root_folder_name="   ")

    def test_zero_timeout_raises(self) -> None:
        with pytest.raises(ValueError, match="request_timeout"):
            FolderManagerConfig(request_timeout=0)

    def test_negative_timeout_raises(self) -> None:
        with pytest.raises(ValueError, match="request_timeout"):
            FolderManagerConfig(request_timeout=-5.0)


# ═════════════════════════════════════════════════════════════════════════
# FolderLayer enum tests
# ═════════════════════════════════════════════════════════════════════════


class TestFolderLayer:
    """Verify FolderLayer enum values."""

    def test_layer_values(self) -> None:
        assert FolderLayer.ROOT.value == "root"
        assert FolderLayer.MEETINGS.value == "meetings"
        assert FolderLayer.MEETING.value == "meeting"
        assert FolderLayer.MEETING_SUB.value == "meeting_sub"
        assert FolderLayer.KNOWLEDGE.value == "knowledge"
        assert FolderLayer.KNOWLEDGE_LAYER.value == "knowledge_layer"
        assert FolderLayer.AGENTS.value == "agents"
        assert FolderLayer.AGENT_SUB.value == "agent_sub"

    def test_layer_count(self) -> None:
        assert len(FolderLayer) == 8


# ═════════════════════════════════════════════════════════════════════════
# Data structure tests
# ═════════════════════════════════════════════════════════════════════════


class TestDataStructures:
    """Verify data class creation and properties."""

    def test_meeting_folder_structure_creation(self) -> None:
        root = GDriveFolder(folder_id="r1", folder_name="Root")
        meetings = GDriveFolder(folder_id="m1", folder_name="meetings")
        meeting = GDriveFolder(folder_id="id1", folder_name="meeting_abc")
        transcripts = GDriveFolder(folder_id="t1", folder_name="transcripts")
        packets = GDriveFolder(folder_id="cp1", folder_name="context_packets")
        decisions = GDriveFolder(folder_id="d1", folder_name="decisions")
        raw = GDriveFolder(folder_id="ro1", folder_name="raw_outputs")

        structure = MeetingFolderStructure(
            meeting_id="meeting_abc",
            root_folder=root,
            meetings_folder=meetings,
            meeting_folder=meeting,
            transcripts_folder=transcripts,
            context_packets_folder=packets,
            decisions_folder=decisions,
            raw_outputs_folder=raw,
            folders_already_existed=False,
        )

        assert structure.meeting_id == "meeting_abc"
        assert structure.root_folder.folder_id == "r1"
        assert structure.folders_already_existed is False

    def test_meeting_folder_structure_sub_folders_property(self) -> None:
        root = GDriveFolder(folder_id="r1", folder_name="R")
        meetings = GDriveFolder(folder_id="m1", folder_name="meetings")
        meeting = GDriveFolder(folder_id="id1", folder_name="meeting_x")
        transcripts = GDriveFolder(folder_id="t1", folder_name="transcripts")
        packets = GDriveFolder(folder_id="cp1", folder_name="context_packets")
        decisions = GDriveFolder(folder_id="d1", folder_name="decisions")
        raw = GDriveFolder(folder_id="ro1", folder_name="raw_outputs")

        structure = MeetingFolderStructure(
            meeting_id="meeting_x",
            root_folder=root,
            meetings_folder=meetings,
            meeting_folder=meeting,
            transcripts_folder=transcripts,
            context_packets_folder=packets,
            decisions_folder=decisions,
            raw_outputs_folder=raw,
        )

        subs = structure.sub_folders
        assert len(subs) == 4
        assert subs["transcripts"].folder_id == "t1"
        assert subs["context_packets"].folder_id == "cp1"
        assert subs["decisions"].folder_id == "d1"
        assert subs["raw_outputs"].folder_id == "ro1"

    def test_knowledge_folder_structure_creation(self) -> None:
        k = GDriveFolder(folder_id="k1", folder_name="knowledge")
        l0 = GDriveFolder(folder_id="l0", folder_name="L0_raw_artifacts")
        l1 = GDriveFolder(folder_id="l1", folder_name="L1_summaries")
        l2 = GDriveFolder(folder_id="l2", folder_name="L2_cross_meeting")
        l3 = GDriveFolder(folder_id="l3", folder_name="L3_organizational")

        structure = KnowledgeFolderStructure(
            knowledge_folder=k,
            L0_folder=l0,
            L1_folder=l1,
            L2_folder=l2,
            L3_folder=l3,
            folders_already_existed=True,
        )

        assert structure.folders_already_existed is True
        assert structure.knowledge_folder.folder_name == "knowledge"

    def test_knowledge_folder_structure_layer_property(self) -> None:
        k = GDriveFolder(folder_id="k1", folder_name="knowledge")
        l0 = GDriveFolder(folder_id="l0", folder_name="L0_raw_artifacts")
        l1 = GDriveFolder(folder_id="l1", folder_name="L1_summaries")
        l2 = GDriveFolder(folder_id="l2", folder_name="L2_cross_meeting")
        l3 = GDriveFolder(folder_id="l3", folder_name="L3_organizational")

        structure = KnowledgeFolderStructure(
            knowledge_folder=k,
            L0_folder=l0, L1_folder=l1, L2_folder=l2, L3_folder=l3,
        )

        layers = structure.layer_folders
        assert len(layers) == 4
        assert layers["L0_raw_artifacts"].folder_id == "l0"
        assert layers["L1_summaries"].folder_id == "l1"
        assert layers["L2_cross_meeting"].folder_id == "l2"
        assert layers["L3_organizational"].folder_id == "l3"

    def test_agent_folder_structure_creation(self) -> None:
        a = GDriveFolder(folder_id="a1", folder_name="agents")
        ps = GDriveFolder(folder_id="ps1", folder_name="persona_specs")
        mc = GDriveFolder(folder_id="mc1", folder_name="model_configs")

        structure = AgentFolderStructure(
            agents_folder=a,
            persona_specs_folder=ps,
            model_configs_folder=mc,
            folders_already_existed=False,
        )

        assert structure.agents_folder.folder_name == "agents"
        assert structure.folders_already_existed is False

    def test_full_system_structure_creation(self) -> None:
        root = GDriveFolder(folder_id="r1", folder_name="Root")
        meetings = GDriveFolder(folder_id="m1", folder_name="meetings")
        k = GDriveFolder(folder_id="k1", folder_name="knowledge")
        l0 = GDriveFolder(folder_id="l0", folder_name="L0_raw_artifacts")
        l1 = GDriveFolder(folder_id="l1", folder_name="L1_summaries")
        l2 = GDriveFolder(folder_id="l2", folder_name="L2_cross_meeting")
        l3 = GDriveFolder(folder_id="l3", folder_name="L3_organizational")
        a = GDriveFolder(folder_id="a1", folder_name="agents")
        ps = GDriveFolder(folder_id="ps1", folder_name="persona_specs")
        mc = GDriveFolder(folder_id="mc1", folder_name="model_configs")

        knowledge = KnowledgeFolderStructure(
            knowledge_folder=k, L0_folder=l0, L1_folder=l1,
            L2_folder=l2, L3_folder=l3, folders_already_existed=True,
        )
        agents = AgentFolderStructure(
            agents_folder=a, persona_specs_folder=ps,
            model_configs_folder=mc, folders_already_existed=True,
        )

        system = FullSystemStructure(
            root_folder=root,
            meetings_container=meetings,
            knowledge_structure=knowledge,
            agent_structure=agents,
            all_folders_existed=True,
        )

        assert system.all_folders_existed is True
        assert system.root_folder.folder_name == "Root"
        assert system.meetings_container.folder_name == "meetings"

    def test_folder_creation_result(self) -> None:
        folder = GDriveFolder(folder_id="f1", folder_name="test")
        result = FolderCreationResult(
            folder=folder,
            layer=FolderLayer.MEETING,
            already_existed=False,
        )

        assert result.folder.folder_id == "f1"
        assert result.layer == FolderLayer.MEETING
        assert result.already_existed is False

    def test_folder_creation_result_already_existed(self) -> None:
        folder = GDriveFolder(folder_id="f2", folder_name="existing")
        result = FolderCreationResult(
            folder=folder,
            layer=FolderLayer.ROOT,
            already_existed=True,
        )

        assert result.already_existed is True


# ═════════════════════════════════════════════════════════════════════════
# Constants tests
# ═════════════════════════════════════════════════════════════════════════


class TestConstants:
    """Verify module-level constants."""

    def test_meeting_sub_folders(self) -> None:
        assert len(MEETING_SUB_FOLDERS) >= 4
        assert "transcripts" in MEETING_SUB_FOLDERS
        assert "context_packets" in MEETING_SUB_FOLDERS
        assert "decisions" in MEETING_SUB_FOLDERS
        assert "raw_outputs" in MEETING_SUB_FOLDERS

    def test_knowledge_layer_folders(self) -> None:
        assert len(KNOWLEDGE_LAYER_FOLDERS) == 4
        assert KNOWLEDGE_LAYER_FOLDERS[0] == "L0_raw_artifacts"
        assert KNOWLEDGE_LAYER_FOLDERS[1] == "L1_summaries"
        assert KNOWLEDGE_LAYER_FOLDERS[2] == "L2_cross_meeting"
        assert KNOWLEDGE_LAYER_FOLDERS[3] == "L3_organizational"

    def test_agent_sub_folders(self) -> None:
        assert len(AGENT_SUB_FOLDERS) == 2
        assert "persona_specs" in AGENT_SUB_FOLDERS
        assert "model_configs" in AGENT_SUB_FOLDERS

    def test_system_folders(self) -> None:
        assert len(SYSTEM_FOLDERS) == 3
        assert "meetings" in SYSTEM_FOLDERS
        assert "knowledge" in SYSTEM_FOLDERS
        assert "agents" in SYSTEM_FOLDERS


# ═════════════════════════════════════════════════════════════════════════
# MeetingFolderManager tests — root folder
# ═════════════════════════════════════════════════════════════════════════


class TestEnsureRootFolder:
    """Verify root folder creation and idempotency."""

    def test_creates_root_folder(self) -> None:
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()
        manager = MeetingFolderManager(_make_config())

        result = manager.ensure_root_folder(token)

        assert result.folder.folder_name == "Test_Meetings"
        assert result.layer == FolderLayer.ROOT
        assert result.already_existed is False
        assert mock.folder_count == 1

    def test_root_folder_idempotent(self) -> None:
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()
        manager = MeetingFolderManager(_make_config())

        result1 = manager.ensure_root_folder(token)
        result2 = manager.ensure_root_folder(token)

        assert result1.already_existed is False
        assert result2.already_existed is True
        assert result1.folder.folder_id == result2.folder.folder_id
        # Only one folder actually created in mock store
        assert mock.folder_count == 1

    def test_root_folder_with_cache_clear_idempotent(self) -> None:
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()
        manager = MeetingFolderManager(_make_config())

        result1 = manager.ensure_root_folder(token)
        manager.clear_cache()
        result2 = manager.ensure_root_folder(token)

        assert result2.already_existed is True
        assert result1.folder.folder_id == result2.folder.folder_id


# ═════════════════════════════════════════════════════════════════════════
# MeetingFolderManager tests — system containers
# ═════════════════════════════════════════════════════════════════════════


class TestEnsureSystemContainers:
    """Verify system container creation and idempotency."""

    def test_creates_all_three_containers(self) -> None:
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()
        manager = MeetingFolderManager(_make_config())

        containers = manager.ensure_system_containers(token)

        assert len(containers) == 3
        names = {c.folder.folder_name for c in containers}
        assert names == {"meetings", "knowledge", "agents"}

        # Verify layers are correct
        layer_names = {c.layer.value for c in containers}
        assert "meetings" in layer_names
        assert "knowledge" in layer_names
        assert "agents" in layer_names

        # Root + 3 containers = 4 folders
        assert mock.folder_count == 4

    def test_system_containers_idempotent(self) -> None:
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()
        manager = MeetingFolderManager(_make_config())

        first = manager.ensure_system_containers(token)
        second = manager.ensure_system_containers(token)

        for c1, c2 in zip(first, second):
            assert c1.folder.folder_id == c2.folder.folder_id
            assert c2.already_existed is True

        assert mock.folder_count == 4  # No new folders created


# ═════════════════════════════════════════════════════════════════════════
# MeetingFolderManager tests — meeting hierarchy
# ═════════════════════════════════════════════════════════════════════════


class TestEnsureMeetingHierarchy:
    """Verify per-meeting folder tree creation and idempotency."""

    def test_full_meeting_hierarchy_created(self) -> None:
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()
        manager = MeetingFolderManager(_make_config())

        structure = manager.ensure_meeting_hierarchy(
            token, "meeting_20260610_test"
        )

        assert structure.meeting_id == "meeting_20260610_test"
        assert structure.root_folder.folder_name == "Test_Meetings"
        assert structure.meetings_folder.folder_name == "meetings"
        assert structure.meeting_folder.folder_name == "meeting_20260610_test"
        assert structure.transcripts_folder.folder_name == "transcripts"
        assert structure.context_packets_folder.folder_name == "context_packets"
        assert structure.decisions_folder.folder_name == "decisions"
        assert structure.raw_outputs_folder.folder_name == "raw_outputs"
        assert structure.folders_already_existed is False

        # Root + meetings + meeting + 4 subs = 7 folders
        assert mock.folder_count == 7

    def test_meeting_hierarchy_idempotent(self) -> None:
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()
        manager = MeetingFolderManager(_make_config())

        first = manager.ensure_meeting_hierarchy(token, "meeting_idem")
        second = manager.ensure_meeting_hierarchy(token, "meeting_idem")

        assert first.folders_already_existed is False
        assert second.folders_already_existed is True
        assert first.meeting_folder.folder_id == second.meeting_folder.folder_id
        assert first.transcripts_folder.folder_id == second.transcripts_folder.folder_id
        assert first.decisions_folder.folder_id == second.decisions_folder.folder_id
        assert mock.folder_count == 7  # No new folders created on second call

    def test_meeting_hierarchy_idempotent_with_cache_clear(self) -> None:
        """Even after cache clear, find-or-create returns existing folders."""
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()
        manager = MeetingFolderManager(_make_config())

        first = manager.ensure_meeting_hierarchy(token, "meeting_cache")
        manager.clear_cache()
        second = manager.ensure_meeting_hierarchy(token, "meeting_cache")

        assert second.folders_already_existed is True
        assert first.meeting_folder.folder_id == second.meeting_folder.folder_id

    def test_meeting_isolation_different_ids(self) -> None:
        """Different meeting_ids must get different folder trees."""
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()
        manager = MeetingFolderManager(_make_config())

        m1 = manager.ensure_meeting_hierarchy(token, "meeting_A")
        m2 = manager.ensure_meeting_hierarchy(token, "meeting_B")

        # Different meeting folders
        assert m1.meeting_folder.folder_id != m2.meeting_folder.folder_id
        assert m1.meeting_folder.folder_name == "meeting_A"
        assert m2.meeting_folder.folder_name == "meeting_B"
        # But share the same root and meetings container
        assert m1.root_folder.folder_id == m2.root_folder.folder_id
        assert m1.meetings_folder.folder_id == m2.meetings_folder.folder_id

    def test_three_meetings_isolation(self) -> None:
        """Three concurrent meetings, each with isolated sub-folders."""
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()
        manager = MeetingFolderManager(_make_config())

        ids = ["meeting_X", "meeting_Y", "meeting_Z"]
        structures = [
            manager.ensure_meeting_hierarchy(token, mid) for mid in ids
        ]

        # All meeting folders must be distinct
        meeting_ids = {s.meeting_folder.folder_id for s in structures}
        assert len(meeting_ids) == 3

        # All share the same root
        root_ids = {s.root_folder.folder_id for s in structures}
        assert len(root_ids) == 1

        # First call: all created. Third call for same meeting: existed.
        m1_again = manager.ensure_meeting_hierarchy(token, ids[0])
        assert m1_again.folders_already_existed is True

    def test_unicode_meeting_id(self) -> None:
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()
        manager = MeetingFolderManager(_make_config())

        structure = manager.ensure_meeting_hierarchy(
            token, "meeting_한글_テスト"
        )

        assert structure.meeting_id == "meeting_한글_テスト"
        assert structure.meeting_folder.folder_name == "meeting_한글_テスト"
        assert structure.folders_already_existed is False

    def test_empty_meeting_id_raises(self) -> None:
        manager = MeetingFolderManager(_make_config())
        token = _make_token()

        with pytest.raises(ValueError, match="meeting_id"):
            manager.ensure_meeting_hierarchy(token, "")

    def test_whitespace_meeting_id_raises(self) -> None:
        manager = MeetingFolderManager(_make_config())
        token = _make_token()

        with pytest.raises(ValueError, match="meeting_id"):
            manager.ensure_meeting_hierarchy(token, "   ")


# ═════════════════════════════════════════════════════════════════════════
# MeetingFolderManager tests — knowledge structure
# ═════════════════════════════════════════════════════════════════════════


class TestEnsureKnowledgeStructure:
    """Verify knowledge layer (L0-L3) folder creation and idempotency."""

    def test_all_four_layers_created(self) -> None:
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()
        manager = MeetingFolderManager(_make_config())

        structure = manager.ensure_knowledge_structure(token)

        assert structure.knowledge_folder.folder_name == "knowledge"
        assert structure.L0_folder.folder_name == "L0_raw_artifacts"
        assert structure.L1_folder.folder_name == "L1_summaries"
        assert structure.L2_folder.folder_name == "L2_cross_meeting"
        assert structure.L3_folder.folder_name == "L3_organizational"
        assert structure.folders_already_existed is False

        # Root + knowledge + 4 layers = 6 folders
        assert mock.folder_count == 6

    def test_knowledge_structure_idempotent(self) -> None:
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()
        manager = MeetingFolderManager(_make_config())

        first = manager.ensure_knowledge_structure(token)
        second = manager.ensure_knowledge_structure(token)

        assert first.folders_already_existed is False
        assert second.folders_already_existed is True
        assert first.L0_folder.folder_id == second.L0_folder.folder_id
        assert first.L3_folder.folder_id == second.L3_folder.folder_id
        assert mock.folder_count == 6

    def test_layer_folders_mapping(self) -> None:
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()
        manager = MeetingFolderManager(_make_config())

        structure = manager.ensure_knowledge_structure(token)

        layers = structure.layer_folders
        assert len(layers) == 4
        assert layers["L0_raw_artifacts"].folder_id == structure.L0_folder.folder_id
        assert layers["L1_summaries"].folder_id == structure.L1_folder.folder_id
        assert layers["L2_cross_meeting"].folder_id == structure.L2_folder.folder_id
        assert layers["L3_organizational"].folder_id == structure.L3_folder.folder_id


# ═════════════════════════════════════════════════════════════════════════
# MeetingFolderManager tests — agent structure
# ═════════════════════════════════════════════════════════════════════════


class TestEnsureAgentStructure:
    """Verify agent storage folder creation and idempotency."""

    def test_agent_folders_created(self) -> None:
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()
        manager = MeetingFolderManager(_make_config())

        structure = manager.ensure_agent_structure(token)

        assert structure.agents_folder.folder_name == "agents"
        assert structure.persona_specs_folder.folder_name == "persona_specs"
        assert structure.model_configs_folder.folder_name == "model_configs"
        assert structure.folders_already_existed is False

        # Root + agents + 2 subs = 4 folders
        assert mock.folder_count == 4

    def test_agent_structure_idempotent(self) -> None:
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()
        manager = MeetingFolderManager(_make_config())

        first = manager.ensure_agent_structure(token)
        second = manager.ensure_agent_structure(token)

        assert first.folders_already_existed is False
        assert second.folders_already_existed is True
        assert first.persona_specs_folder.folder_id == second.persona_specs_folder.folder_id
        assert mock.folder_count == 4


# ═════════════════════════════════════════════════════════════════════════
# MeetingFolderManager tests — full system
# ═════════════════════════════════════════════════════════════════════════


class TestEnsureFullSystem:
    """Verify full system initialization and idempotency."""

    def test_full_system_created(self) -> None:
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()
        manager = MeetingFolderManager(_make_config())

        system = manager.ensure_full_system(token)

        assert system.root_folder.folder_name == "Test_Meetings"
        assert system.meetings_container.folder_name == "meetings"
        assert system.knowledge_structure.L0_folder.folder_name == "L0_raw_artifacts"
        assert system.knowledge_structure.L3_folder.folder_name == "L3_organizational"
        assert system.agent_structure.persona_specs_folder.folder_name == "persona_specs"
        assert system.all_folders_existed is False

        # Root + 3 containers + 4 knowledge layers + 2 agent subs = 10
        assert mock.folder_count == 10

    def test_full_system_idempotent(self) -> None:
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()
        manager = MeetingFolderManager(_make_config())

        first = manager.ensure_full_system(token)
        second = manager.ensure_full_system(token)

        assert first.all_folders_existed is False
        assert second.all_folders_existed is True
        assert mock.folder_count == 10

    def test_full_system_then_meeting_idempotent(self) -> None:
        """After full system init, individual meeting creation reuses containers."""
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()
        manager = MeetingFolderManager(_make_config())

        manager.ensure_full_system(token)
        count_after_system = mock.folder_count

        # Now create a meeting — should reuse existing meetings container
        meeting = manager.ensure_meeting_hierarchy(token, "meeting_after_full")

        # The root, meetings container, etc. already existed
        # New: meeting folder + 4 subs = 5 new folders
        assert mock.folder_count == count_after_system + 5
        # The meeting result should show meetings container already existed
        assert meeting.folders_already_existed is False  # meeting folders are new


# ═════════════════════════════════════════════════════════════════════════
# MeetingFolderManager tests — list and verify
# ═════════════════════════════════════════════════════════════════════════


class TestListAndVerify:
    """Verify read-only folder listing and verification methods."""

    def test_list_meeting_sub_folders_returns_all(self) -> None:
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()
        manager = MeetingFolderManager(_make_config())

        manager.ensure_meeting_hierarchy(token, "meeting_list")

        subs = manager.list_meeting_sub_folders(token, "meeting_list")

        assert len(subs) == 4
        assert "transcripts" in subs
        assert "context_packets" in subs
        assert "decisions" in subs
        assert "raw_outputs" in subs

    def test_list_meeting_sub_folders_nonexistent_meeting(self) -> None:
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()
        manager = MeetingFolderManager(_make_config())

        subs = manager.list_meeting_sub_folders(token, "no_such_meeting")

        assert subs == {}

    def test_list_meeting_sub_folders_no_root(self) -> None:
        """When root folder doesn't exist, returns empty dict."""
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()
        manager = MeetingFolderManager(_make_config())

        # Don't create anything first
        subs = manager.list_meeting_sub_folders(token, "anything")

        assert subs == {}

    def test_verify_hierarchy_exists_true(self) -> None:
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()
        manager = MeetingFolderManager(_make_config())

        manager.ensure_meeting_hierarchy(token, "meeting_verify")

        assert manager.verify_hierarchy_exists(token, "meeting_verify") is True

    def test_verify_hierarchy_exists_false(self) -> None:
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()
        manager = MeetingFolderManager(_make_config())

        # No meeting created
        assert manager.verify_hierarchy_exists(token, "nonexistent") is False

    def test_verify_hierarchy_exists_partial(self) -> None:
        """If only some sub-folders exist, verify returns False."""
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()
        manager = MeetingFolderManager(_make_config())

        # Create root and meetings container manually, but no meeting
        root_result = manager.ensure_root_folder(token)
        manager._ensure_folder(
            token, "meetings", parent_id=root_result.folder.folder_id
        )

        assert manager.verify_hierarchy_exists(token, "partial_meeting") is False


# ═════════════════════════════════════════════════════════════════════════
# MeetingFolderManager tests — cache management
# ═════════════════════════════════════════════════════════════════════════


class TestCacheManagement:
    """Verify folder cache behaviour."""

    def test_cache_grows_with_operations(self) -> None:
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()
        manager = MeetingFolderManager(_make_config())

        assert manager.cache_size() == 0

        manager.ensure_root_folder(token)
        assert manager.cache_size() == 1

        manager.ensure_meeting_hierarchy(token, "meeting_c1")
        # Root + meetings + meeting + 4 subs = 7 cache entries
        assert manager.cache_size() == 7

    def test_clear_cache_resets_to_zero(self) -> None:
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()
        manager = MeetingFolderManager(_make_config())

        manager.ensure_full_system(token)
        assert manager.cache_size() > 0

        manager.clear_cache()
        assert manager.cache_size() == 0

    def test_cache_after_multiple_meetings(self) -> None:
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()
        manager = MeetingFolderManager(_make_config())

        manager.ensure_meeting_hierarchy(token, "meeting_A")
        manager.ensure_meeting_hierarchy(token, "meeting_B")

        # Shared: root + meetings = 2
        # meeting_A: meeting_A folder + 4 subs = 5
        # meeting_B: meeting_B folder + 4 subs = 5
        # Total: 12
        assert manager.cache_size() == 12

    def test_cache_reuse_prevents_creation(self) -> None:
        """Cached folders are returned without re-creating."""
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()
        manager = MeetingFolderManager(_make_config())

        first = manager.ensure_meeting_hierarchy(token, "meeting_cached")
        first_count = mock.folder_count

        # Second call uses cache — no new Drive calls for cached entries
        second = manager.ensure_meeting_hierarchy(token, "meeting_cached")
        assert mock.folder_count == first_count
        assert second.folders_already_existed is True


# ═════════════════════════════════════════════════════════════════════════
# Error path tests
# ═════════════════════════════════════════════════════════════════════════


class TestErrorPaths:
    """Verify error handling for various failure modes."""

    def test_root_folder_api_error(self) -> None:
        inject_drive_api(_mock_drive_server_error)
        token = _make_token()
        manager = MeetingFolderManager(_make_config())

        with pytest.raises(ValueError, match="Failed to create folder"):
            manager.ensure_root_folder(token)

    def test_meeting_hierarchy_root_api_error(self) -> None:
        inject_drive_api(_mock_drive_server_error)
        token = _make_token()
        manager = MeetingFolderManager(_make_config())

        with pytest.raises(ValueError, match="Failed to create folder"):
            manager.ensure_meeting_hierarchy(token, "meeting_fail")

    def test_network_failure(self) -> None:
        inject_drive_api(_mock_drive_network_failure)
        token = _make_token()
        manager = MeetingFolderManager(_make_config())

        with pytest.raises(OSError, match="Connection reset"):
            manager.ensure_root_folder(token)

    def test_knowledge_structure_api_error(self) -> None:
        """When root creation succeeds but knowledge folder creation fails."""
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()
        manager = MeetingFolderManager(_make_config())

        # First succeed with root
        manager.ensure_root_folder(token)

        # Then fail
        inject_drive_api(_mock_drive_server_error)
        with pytest.raises(ValueError, match="Failed to create folder"):
            manager.ensure_knowledge_structure(token)


# ═════════════════════════════════════════════════════════════════════════
# Idempotency stress tests
# ═════════════════════════════════════════════════════════════════════════


class TestIdempotencyStress:
    """Verify idempotency holds under repeated calls."""

    def test_many_repeated_meeting_calls(self) -> None:
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()
        manager = MeetingFolderManager(_make_config())

        meeting_id = "meeting_stress"

        # First call creates
        first = manager.ensure_meeting_hierarchy(token, meeting_id)
        assert first.folders_already_existed is False
        count_after_first = mock.folder_count

        # 50 repeated calls — all should be idempotent
        for i in range(50):
            result = manager.ensure_meeting_hierarchy(token, meeting_id)
            assert result.folders_already_existed is True
            assert result.meeting_folder.folder_id == first.meeting_folder.folder_id

        # No new folders should have been created
        assert mock.folder_count == count_after_first

    def test_interleaved_meeting_creations_idempotent(self) -> None:
        """Creating meetings A, B, A, B should be fully idempotent."""
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()
        manager = MeetingFolderManager(_make_config())

        a1 = manager.ensure_meeting_hierarchy(token, "meeting_A")
        b1 = manager.ensure_meeting_hierarchy(token, "meeting_B")
        a2 = manager.ensure_meeting_hierarchy(token, "meeting_A")
        b2 = manager.ensure_meeting_hierarchy(token, "meeting_B")

        assert a1.folders_already_existed is False
        assert b1.folders_already_existed is False
        assert a2.folders_already_existed is True
        assert b2.folders_already_existed is True

        assert a1.meeting_folder.folder_id == a2.meeting_folder.folder_id
        assert b1.meeting_folder.folder_id == b2.meeting_folder.folder_id

    def test_full_system_stress_idempotent(self) -> None:
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()
        manager = MeetingFolderManager(_make_config())

        first = manager.ensure_full_system(token)
        assert first.all_folders_existed is False

        for _ in range(20):
            result = manager.ensure_full_system(token)
            assert result.all_folders_existed is True

        # Should be exactly 10 folders total
        assert mock.folder_count == 10


# ═════════════════════════════════════════════════════════════════════════
# build_folder_manager convenience function
# ═════════════════════════════════════════════════════════════════════════


class TestBuildFolderManager:
    """Verify the module-level build_folder_manager convenience function."""

    def test_default_parameters(self) -> None:
        manager = build_folder_manager()
        assert manager.config.root_folder_name == DEFAULT_ROOT_FOLDER_NAME
        assert manager.config.request_timeout == DEFAULT_REQUEST_TIMEOUT

    def test_custom_parameters(self) -> None:
        manager = build_folder_manager(
            root_folder_name="MyCustomRoot",
            request_timeout=25.0,
        )
        assert manager.config.root_folder_name == "MyCustomRoot"
        assert manager.config.request_timeout == 25.0


# ═════════════════════════════════════════════════════════════════════════
# Folder structure completeness tests
# ═════════════════════════════════════════════════════════════════════════


class TestFolderStructureCompleteness:
    """Verify folder structures contain all expected items."""

    def test_meeting_structure_has_all_sub_folders(self) -> None:
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()
        manager = MeetingFolderManager(_make_config())

        structure = manager.ensure_meeting_hierarchy(token, "meeting_complete")

        subs = structure.sub_folders
        for expected in MEETING_SUB_FOLDERS:
            assert expected in subs, f"Missing {expected} in meeting sub-folders"
            assert subs[expected].folder_id != ""

    def test_knowledge_structure_has_all_layers(self) -> None:
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()
        manager = MeetingFolderManager(_make_config())

        structure = manager.ensure_knowledge_structure(token)

        layers = structure.layer_folders
        for expected in KNOWLEDGE_LAYER_FOLDERS:
            assert expected in layers, f"Missing {expected} in knowledge layers"
            assert layers[expected].folder_id != ""

    def test_full_system_nested_completeness(self) -> None:
        """Verify that after ensure_full_system, all subsystems are complete."""
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()
        manager = MeetingFolderManager(_make_config())

        system = manager.ensure_full_system(token)

        # Knowledge layers all present
        k_layers = system.knowledge_structure.layer_folders
        assert len(k_layers) == 4
        for name in KNOWLEDGE_LAYER_FOLDERS:
            assert name in k_layers

        # Agent sub-folders all present
        assert system.agent_structure.persona_specs_folder.folder_id != ""
        assert system.agent_structure.model_configs_folder.folder_id != ""

        # Meetings container is present
        assert system.meetings_container.folder_id != ""


# ═════════════════════════════════════════════════════════════════════════
# Custom root name tests
# ═════════════════════════════════════════════════════════════════════════


class TestCustomRootName:
    """Verify behaviour with non-default root folder names."""

    def test_custom_root_meeting_hierarchy(self) -> None:
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()
        manager = MeetingFolderManager(
            _make_config(root_folder_name="CustomCorp")
        )

        structure = manager.ensure_meeting_hierarchy(token, "meeting_custom")

        assert structure.root_folder.folder_name == "CustomCorp"

    def test_custom_root_knowledge_structure(self) -> None:
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()
        manager = MeetingFolderManager(
            _make_config(root_folder_name="CompanyX_KB")
        )

        structure = manager.ensure_knowledge_structure(token)

        # Root is correct
        root = _find_folder(token, "CompanyX_KB")
        assert root is not None
        assert root.folder_name == "CompanyX_KB"

        # Knowledge folder is under custom root
        knowledge = _find_folder(
            token, "knowledge", parent_id=root.folder_id
        )
        assert knowledge is not None

    def test_different_roots_are_isolated(self) -> None:
        """Two managers with different root names get different folders."""
        mock = MockDriveAPI()
        inject_drive_api(mock.handle)
        token = _make_token()

        mgr_a = MeetingFolderManager(_make_config(root_folder_name="RootA"))
        mgr_b = MeetingFolderManager(_make_config(root_folder_name="RootB"))

        struct_a = mgr_a.ensure_meeting_hierarchy(token, "meeting_shared")
        struct_b = mgr_b.ensure_meeting_hierarchy(token, "meeting_shared")

        # Different root folders
        assert struct_a.root_folder.folder_id != struct_b.root_folder.folder_id
        assert struct_a.root_folder.folder_name == "RootA"
        assert struct_b.root_folder.folder_name == "RootB"
        # Different meeting folders (different parents)
        assert struct_a.meeting_folder.folder_id != struct_b.meeting_folder.folder_id
