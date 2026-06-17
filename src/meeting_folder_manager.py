"""Meeting folder structure manager (Sub-AC 19a-4).

Creates and maintains per-meeting folder hierarchies on Google Drive,
supporting the 4-layer knowledge accumulation system (L0-L3) and
complete meeting_id-based directory isolation.

Design principles:
- Idempotent: calling create/ensure repeatedly is safe (find-or-create)
- Resumable: folder cache survives multiple calls within a session
- Isolated: each meeting_id gets its own complete subtree
- Testable: uses the same inject_drive_api() pattern as gdrive_artifact_writer

Folder hierarchy::

    {root_folder_name}/
      meetings/
        {meeting_id}/
          transcripts/
          context_packets/
          decisions/
          raw_outputs/
      knowledge/
        L0_raw_artifacts/
        L1_summaries/
        L2_cross_meeting/
        L3_organizational/
      agents/
        persona_specs/
        model_configs/

Usage::

    from src.meeting_folder_manager import (
        MeetingFolderManager, FolderManagerConfig, FolderLayer,
    )
    from src.gdrive_auth import GDriveAuthenticator

    config = FolderManagerConfig(root_folder_name="AI_Agent_Meetings")
    manager = MeetingFolderManager(config)

    token = auth.get_valid_token().token

    # Create full meeting hierarchy
    result = manager.ensure_meeting_hierarchy(
        token=token, meeting_id="meeting_20260610_abc123"
    )
    print(f"Meeting folder: {result.meeting_folder.folder_id}")
    print(f"Transcripts sub-folder: {result.transcripts_folder.folder_id}")

    # Second call is idempotent
    result2 = manager.ensure_meeting_hierarchy(
        token=token, meeting_id="meeting_20260610_abc123"
    )
    assert result2.folders_already_existed is True
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

from .gdrive_artifact_writer import (
    DEFAULT_ROOT_FOLDER_NAME,
    DEFAULT_REQUEST_TIMEOUT,
    FOLDER_MIME_TYPE,
    GDriveFolder,
    _find_folder,
    _get_drive_api_fn,
    find_or_create_folder,
    inject_drive_api,
)
from .gdrive_auth import GDriveToken

# ═════════════════════════════════════════════════════════════════════════
# Constants
# ═════════════════════════════════════════════════════════════════════════

# Sub-folder names within a meeting directory
MEETING_SUB_FOLDERS: tuple[str, ...] = (
    "transcripts",
    "context_packets",
    "decisions",
    "raw_outputs",
)

# Knowledge layer folder names
KNOWLEDGE_LAYER_FOLDERS: tuple[str, ...] = (
    "L0_raw_artifacts",
    "L1_summaries",
    "L2_cross_meeting",
    "L3_organizational",
)

# Agent directory sub-folders
AGENT_SUB_FOLDERS: tuple[str, ...] = (
    "persona_specs",
    "model_configs",
)

# Top-level structural folders
SYSTEM_FOLDERS: tuple[str, ...] = (
    "meetings",
    "knowledge",
    "agents",
)


class FolderLayer(str, Enum):
    """Identifies which structural layer a folder belongs to."""

    ROOT = "root"
    MEETINGS = "meetings"
    MEETING = "meeting"
    MEETING_SUB = "meeting_sub"
    KNOWLEDGE = "knowledge"
    KNOWLEDGE_LAYER = "knowledge_layer"
    AGENTS = "agents"
    AGENT_SUB = "agent_sub"


# ═════════════════════════════════════════════════════════════════════════
# Data types
# ═════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class FolderManagerConfig:
    """Configuration for the meeting folder structure manager.

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
class MeetingFolderStructure:
    """Complete folder structure for a single meeting.

    Contains resolved folder references for every sub-directory
    within a meeting's isolated folder tree.

    Attributes:
        meeting_id: The meeting identifier.
        root_folder: The top-level Drive folder.
        meetings_folder: The ``meetings/`` container folder.
        meeting_folder: The per-meeting folder (``{meeting_id}/``).
        transcripts_folder: Sub-folder for meeting transcripts.
        context_packets_folder: Sub-folder for context packets.
        decisions_folder: Sub-folder for decision records.
        raw_outputs_folder: Sub-folder for raw LLM outputs.
        folders_already_existed: True if ALL folders were found
            (not created) — indicates full idempotency.
    """

    meeting_id: str
    root_folder: GDriveFolder
    meetings_folder: GDriveFolder
    meeting_folder: GDriveFolder
    transcripts_folder: GDriveFolder
    context_packets_folder: GDriveFolder
    decisions_folder: GDriveFolder
    raw_outputs_folder: GDriveFolder
    folders_already_existed: bool = False

    @property
    def sub_folders(self) -> dict[str, GDriveFolder]:
        """Return a mapping of sub-folder name to GDriveFolder."""
        return {
            "transcripts": self.transcripts_folder,
            "context_packets": self.context_packets_folder,
            "decisions": self.decisions_folder,
            "raw_outputs": self.raw_outputs_folder,
        }


@dataclass(frozen=True)
class KnowledgeFolderStructure:
    """Complete folder structure for the knowledge accumulation system.

    Attributes:
        knowledge_folder: The ``knowledge/`` container folder.
        L0_folder: Raw artifacts layer folder.
        L1_folder: Summaries layer folder.
        L2_folder: Cross-meeting synthesis layer folder.
        L3_folder: Organizational memory layer folder.
        folders_already_existed: True if ALL folders were found.
    """

    knowledge_folder: GDriveFolder
    L0_folder: GDriveFolder
    L1_folder: GDriveFolder
    L2_folder: GDriveFolder
    L3_folder: GDriveFolder
    folders_already_existed: bool = False

    @property
    def layer_folders(self) -> dict[str, GDriveFolder]:
        """Return a mapping of layer name to GDriveFolder."""
        return {
            "L0_raw_artifacts": self.L0_folder,
            "L1_summaries": self.L1_folder,
            "L2_cross_meeting": self.L2_folder,
            "L3_organizational": self.L3_folder,
        }


@dataclass(frozen=True)
class AgentFolderStructure:
    """Folder structure for agent persona and model configuration storage.

    Attributes:
        agents_folder: The ``agents/`` container folder.
        persona_specs_folder: Folder for agent persona specifications.
        model_configs_folder: Folder for per-agent model configurations.
        folders_already_existed: True if ALL folders were found.
    """

    agents_folder: GDriveFolder
    persona_specs_folder: GDriveFolder
    model_configs_folder: GDriveFolder
    folders_already_existed: bool = False


@dataclass(frozen=True)
class FullSystemStructure:
    """Complete system-wide folder structure.

    A snapshot of the entire Drive folder hierarchy managed
    by the folder manager.

    Attributes:
        root_folder: The top-level Drive folder.
        meetings_container: The ``meetings/`` container folder.
        knowledge_structure: Resolved knowledge layer folders.
        agent_structure: Resolved agent storage folders.
        all_folders_existed: True when every single folder was found
            (no creates occurred) — full system idempotency.
    """

    root_folder: GDriveFolder
    meetings_container: GDriveFolder
    knowledge_structure: KnowledgeFolderStructure
    agent_structure: AgentFolderStructure
    all_folders_existed: bool = False


@dataclass(frozen=True)
class FolderCreationResult:
    """Fine-grained result of a folder ensure operation.

    Reports which folders were found vs. created, enabling
    detailed idempotency verification in tests.

    Attributes:
        folder: The resolved folder.
        layer: Which structural layer this folder belongs to.
        already_existed: True if the folder was found (not created).
    """

    folder: GDriveFolder
    layer: FolderLayer
    already_existed: bool


# ═════════════════════════════════════════════════════════════════════════
# Folder structure manager
# ═════════════════════════════════════════════════════════════════════════


class MeetingFolderManager:
    """Manages the complete folder hierarchy for the meeting system on Google Drive.

    Provides idempotent creation and lookup of:
    - Per-meeting folder trees (isolated by meeting_id)
    - Knowledge accumulation layer folders (L0-L3)
    - Agent persona and model config folders
    - Full system structure initialization

    All folder operations are idempotent — repeated calls with the same
    parameters will return existing folders without creating duplicates.

    Usage::

        config = FolderManagerConfig(root_folder_name="AI_Agent_Meetings")
        manager = MeetingFolderManager(config)

        # Ensure a single meeting's folder tree exists
        meeting = manager.ensure_meeting_hierarchy(token, "meeting_abc123")

        # Ensure the knowledge layer structure exists
        knowledge = manager.ensure_knowledge_structure(token)

        # Initialize the entire system hierarchy
        system = manager.ensure_full_system(token)
    """

    def __init__(self, config: FolderManagerConfig | None = None) -> None:
        self._config = config or FolderManagerConfig()
        self._folder_cache: dict[str, GDriveFolder] = {}

    @property
    def config(self) -> FolderManagerConfig:
        """Return the manager's configuration."""
        return self._config

    # ── Internal helpers ──────────────────────────────────────────────

    def _ensure_folder(
        self,
        token: GDriveToken,
        folder_name: str,
        parent_id: str | None = None,
        *,
        layer: FolderLayer = FolderLayer.ROOT,
    ) -> FolderCreationResult:
        """Ensure a single folder exists, with caching.

        Returns a ``FolderCreationResult`` with the folder and whether
        it already existed.
        """
        cache_key = f"{parent_id or 'root'}/{folder_name}"
        if cache_key in self._folder_cache:
            return FolderCreationResult(
                folder=self._folder_cache[cache_key],
                layer=layer,
                already_existed=True,
            )

        timeout = self._config.request_timeout
        folder, created = find_or_create_folder(
            token, folder_name, parent_id=parent_id, timeout=timeout
        )
        self._folder_cache[cache_key] = folder
        return FolderCreationResult(
            folder=folder,
            layer=layer,
            already_existed=not created,
        )

    def _ensure_folders_batch(
        self,
        token: GDriveToken,
        folder_names: tuple[str, ...],
        parent_id: str,
        *,
        layer: FolderLayer = FolderLayer.ROOT,
    ) -> list[FolderCreationResult]:
        """Ensure multiple sibling folders exist under a single parent.

        Returns a list of ``FolderCreationResult``, one per folder.
        """
        results: list[FolderCreationResult] = []
        for name in folder_names:
            result = self._ensure_folder(
                token, name, parent_id=parent_id, layer=layer
            )
            results.append(result)
        return results

    # ── Root folder ───────────────────────────────────────────────────

    def ensure_root_folder(
        self,
        token: GDriveToken,
    ) -> FolderCreationResult:
        """Ensure the top-level root folder exists.

        Returns:
            ``FolderCreationResult`` for the root folder.
        """
        return self._ensure_folder(
            token,
            self._config.root_folder_name,
            layer=FolderLayer.ROOT,
        )

    # ── System containers ─────────────────────────────────────────────

    def ensure_system_containers(
        self,
        token: GDriveToken,
    ) -> tuple[FolderCreationResult, ...]:
        """Ensure the three top-level system containers exist.

        Creates (or finds) ``meetings/``, ``knowledge/``, and
        ``agents/`` under the root folder.

        Returns:
            A 3-tuple of ``FolderCreationResult`` for
            (meetings, knowledge, agents) in that order.
        """
        root = self.ensure_root_folder(token)
        results = self._ensure_folders_batch(
            token,
            SYSTEM_FOLDERS,
            parent_id=root.folder.folder_id,
            layer=FolderLayer.MEETINGS,
        )
        # Re-tag each result with its specific layer
        layer_map = {
            "meetings": FolderLayer.MEETINGS,
            "knowledge": FolderLayer.KNOWLEDGE,
            "agents": FolderLayer.AGENTS,
        }
        tagged: list[FolderCreationResult] = []
        for r in results:
            tagged.append(
                FolderCreationResult(
                    folder=r.folder,
                    layer=layer_map.get(r.folder.folder_name, r.layer),
                    already_existed=r.already_existed,
                )
            )
        return tuple(tagged)

    # ── Meeting hierarchy ─────────────────────────────────────────────

    def ensure_meeting_hierarchy(
        self,
        token: GDriveToken,
        meeting_id: str,
    ) -> MeetingFolderStructure:
        """Ensure the complete folder tree for a single meeting exists.

        Creates (or finds)::

          {root}/meetings/{meeting_id}/
            transcripts/
            context_packets/
            decisions/
            raw_outputs/

        The operation is fully idempotent — if all folders already
        exist, ``folders_already_existed`` will be True.

        Args:
            token: Valid ``GDriveToken``.
            meeting_id: Unique meeting identifier.

        Returns:
            ``MeetingFolderStructure`` with all folder references.

        Raises:
            ValueError: On Drive API errors.
            OSError: On network failure.
        """
        if not meeting_id.strip():
            raise ValueError("meeting_id must not be empty")

        # Ensure root and meetings container
        root = self.ensure_root_folder(token)
        meetings_result = self._ensure_folder(
            token,
            "meetings",
            parent_id=root.folder.folder_id,
            layer=FolderLayer.MEETINGS,
        )

        # Ensure the meeting-specific folder
        meeting_result = self._ensure_folder(
            token,
            meeting_id,
            parent_id=meetings_result.folder.folder_id,
            layer=FolderLayer.MEETING,
        )

        # Ensure all sub-folders under the meeting folder
        sub_results = self._ensure_folders_batch(
            token,
            MEETING_SUB_FOLDERS,
            parent_id=meeting_result.folder.folder_id,
            layer=FolderLayer.MEETING_SUB,
        )

        # Build the sub-folder lookup
        sub_map: dict[str, GDriveFolder] = {}
        for sr in sub_results:
            sub_map[sr.folder.folder_name] = sr.folder

        # Determine overall idempotency
        all_existed = (
            root.already_existed
            and meetings_result.already_existed
            and meeting_result.already_existed
            and all(sr.already_existed for sr in sub_results)
        )

        return MeetingFolderStructure(
            meeting_id=meeting_id,
            root_folder=root.folder,
            meetings_folder=meetings_result.folder,
            meeting_folder=meeting_result.folder,
            transcripts_folder=sub_map.get(
                "transcripts",
                GDriveFolder(folder_id="", folder_name="transcripts"),
            ),
            context_packets_folder=sub_map.get(
                "context_packets",
                GDriveFolder(folder_id="", folder_name="context_packets"),
            ),
            decisions_folder=sub_map.get(
                "decisions",
                GDriveFolder(folder_id="", folder_name="decisions"),
            ),
            raw_outputs_folder=sub_map.get(
                "raw_outputs",
                GDriveFolder(folder_id="", folder_name="raw_outputs"),
            ),
            folders_already_existed=all_existed,
        )

    def list_meeting_sub_folders(
        self,
        token: GDriveToken,
        meeting_id: str,
    ) -> dict[str, GDriveFolder]:
        """Return the sub-folders of a meeting without creating anything.

        If the meeting hierarchy does not exist, returns an empty dict.

        Args:
            token: Valid ``GDriveToken``.
            meeting_id: Meeting identifier.

        Returns:
            Dict mapping sub-folder name to ``GDriveFolder``.
            Empty if the meeting folder does not exist.
        """
        if not meeting_id.strip():
            raise ValueError("meeting_id must not be empty")

        timeout = self._config.request_timeout

        # Walk the hierarchy without creating
        root = _find_folder(
            token, self._config.root_folder_name, timeout=timeout
        )
        if root is None:
            return {}

        meetings = _find_folder(
            token, "meetings", parent_id=root.folder_id, timeout=timeout
        )
        if meetings is None:
            return {}

        meeting = _find_folder(
            token, meeting_id, parent_id=meetings.folder_id, timeout=timeout
        )
        if meeting is None:
            return {}

        result: dict[str, GDriveFolder] = {}
        for sub_name in MEETING_SUB_FOLDERS:
            sub = _find_folder(
                token, sub_name, parent_id=meeting.folder_id, timeout=timeout
            )
            if sub is not None:
                result[sub_name] = sub

        return result

    # ── Knowledge structure ───────────────────────────────────────────

    def ensure_knowledge_structure(
        self,
        token: GDriveToken,
    ) -> KnowledgeFolderStructure:
        """Ensure the 4-layer knowledge accumulation folder structure exists.

        Creates (or finds)::

          {root}/knowledge/
            L0_raw_artifacts/
            L1_summaries/
            L2_cross_meeting/
            L3_organizational/

        This supports the Seed requirement for 4-layer knowledge
        accumulation across meetings.

        Args:
            token: Valid ``GDriveToken``.

        Returns:
            ``KnowledgeFolderStructure`` with all layer folder references.
        """
        root = self.ensure_root_folder(token)
        knowledge_result = self._ensure_folder(
            token,
            "knowledge",
            parent_id=root.folder.folder_id,
            layer=FolderLayer.KNOWLEDGE,
        )

        layer_results = self._ensure_folders_batch(
            token,
            KNOWLEDGE_LAYER_FOLDERS,
            parent_id=knowledge_result.folder.folder_id,
            layer=FolderLayer.KNOWLEDGE_LAYER,
        )

        layer_map: dict[str, GDriveFolder] = {}
        for lr in layer_results:
            layer_map[lr.folder.folder_name] = lr.folder

        all_existed = (
            root.already_existed
            and knowledge_result.already_existed
            and all(lr.already_existed for lr in layer_results)
        )

        return KnowledgeFolderStructure(
            knowledge_folder=knowledge_result.folder,
            L0_folder=layer_map.get(
                "L0_raw_artifacts",
                GDriveFolder(folder_id="", folder_name="L0_raw_artifacts"),
            ),
            L1_folder=layer_map.get(
                "L1_summaries",
                GDriveFolder(folder_id="", folder_name="L1_summaries"),
            ),
            L2_folder=layer_map.get(
                "L2_cross_meeting",
                GDriveFolder(folder_id="", folder_name="L2_cross_meeting"),
            ),
            L3_folder=layer_map.get(
                "L3_organizational",
                GDriveFolder(folder_id="", folder_name="L3_organizational"),
            ),
            folders_already_existed=all_existed,
        )

    # ── Agent structure ───────────────────────────────────────────────

    def ensure_agent_structure(
        self,
        token: GDriveToken,
    ) -> AgentFolderStructure:
        """Ensure the agent storage folder structure exists.

        Creates (or finds)::

          {root}/agents/
            persona_specs/
            model_configs/

        Args:
            token: Valid ``GDriveToken``.

        Returns:
            ``AgentFolderStructure`` with agent folder references.
        """
        root = self.ensure_root_folder(token)
        agents_result = self._ensure_folder(
            token,
            "agents",
            parent_id=root.folder.folder_id,
            layer=FolderLayer.AGENTS,
        )

        sub_results = self._ensure_folders_batch(
            token,
            AGENT_SUB_FOLDERS,
            parent_id=agents_result.folder.folder_id,
            layer=FolderLayer.AGENT_SUB,
        )

        sub_map: dict[str, GDriveFolder] = {}
        for sr in sub_results:
            sub_map[sr.folder.folder_name] = sr.folder

        all_existed = (
            root.already_existed
            and agents_result.already_existed
            and all(sr.already_existed for sr in sub_results)
        )

        return AgentFolderStructure(
            agents_folder=agents_result.folder,
            persona_specs_folder=sub_map.get(
                "persona_specs",
                GDriveFolder(folder_id="", folder_name="persona_specs"),
            ),
            model_configs_folder=sub_map.get(
                "model_configs",
                GDriveFolder(folder_id="", folder_name="model_configs"),
            ),
            folders_already_existed=all_existed,
        )

    # ── Full system ───────────────────────────────────────────────────

    def ensure_full_system(
        self,
        token: GDriveToken,
    ) -> FullSystemStructure:
        """Ensure the entire system folder hierarchy exists.

        Creates (or finds) all structural folders in one call:
        - System containers (meetings, knowledge, agents)
        - Knowledge layers (L0-L3)
        - Agent sub-folders (persona_specs, model_configs)

        This is the entry point for initial system setup.

        Args:
            token: Valid ``GDriveToken``.

        Returns:
            ``FullSystemStructure`` with all folder references.
        """
        root = self.ensure_root_folder(token)
        containers = self.ensure_system_containers(token)

        # Extract the meetings container (first element)
        meetings_container = containers[0].folder

        knowledge = self.ensure_knowledge_structure(token)
        agents = self.ensure_agent_structure(token)

        all_existed = (
            root.already_existed
            and all(c.already_existed for c in containers)
            and knowledge.folders_already_existed
            and agents.folders_already_existed
        )

        return FullSystemStructure(
            root_folder=root.folder,
            meetings_container=meetings_container,
            knowledge_structure=knowledge,
            agent_structure=agents,
            all_folders_existed=all_existed,
        )

    # ── Cache management ──────────────────────────────────────────────

    def clear_cache(self) -> None:
        """Clear the internal folder ID cache.

        Useful between test runs or when Drive state may have changed
        externally.  After clearing, the next folder lookup will
        perform a real Drive API call.
        """
        self._folder_cache.clear()

    def cache_size(self) -> int:
        """Return the number of cached folder entries."""
        return len(self._folder_cache)

    # ── Diagnostics ───────────────────────────────────────────────────

    def verify_hierarchy_exists(
        self,
        token: GDriveToken,
        meeting_id: str,
    ) -> bool:
        """Check whether a meeting's complete folder hierarchy exists.

        Returns True only if every expected folder in the meeting tree
        is present.  Does NOT create missing folders.

        Args:
            token: Valid ``GDriveToken``.
            meeting_id: Meeting identifier.

        Returns:
            True if the complete hierarchy exists.
        """
        sub_folders = self.list_meeting_sub_folders(token, meeting_id)
        expected = set(MEETING_SUB_FOLDERS)
        actual = set(sub_folders.keys())
        return expected == actual


# ═════════════════════════════════════════════════════════════════════════
# Module-level convenience
# ═════════════════════════════════════════════════════════════════════════


def build_folder_manager(
    root_folder_name: str = DEFAULT_ROOT_FOLDER_NAME,
    request_timeout: float = DEFAULT_REQUEST_TIMEOUT,
) -> MeetingFolderManager:
    """Build a ``MeetingFolderManager`` from individual parameters.

    Args:
        root_folder_name: Top-level folder name in Google Drive.
        request_timeout: HTTP request timeout in seconds.

    Returns:
        A configured ``MeetingFolderManager`` instance.
    """
    config = FolderManagerConfig(
        root_folder_name=root_folder_name,
        request_timeout=request_timeout,
    )
    return MeetingFolderManager(config)
