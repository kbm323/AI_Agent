"""Shared immutable values used by the LLM Wiki workflow."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True)
class LlmWikiSource:
    normalized_url: str
    source_type: str
    title: str
    content: str
    retrieved_at: str = ""
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class LlmWikiSummary:
    title: str
    summary: str
    key_points: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    user_perspective: str = ""


@dataclass(frozen=True)
class LlmWikiWriteResult:
    ok: bool
    status: str = ""
    record_id: str = ""
    raw_path: str = ""
    canonical_path: str = ""
    error: str = ""
