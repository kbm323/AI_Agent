"""Dynamic relevant-knowledge retrieval for AC20.

The Coordinator should retrieve a small relevant subset per meeting rather
than blanket-injecting all memory.  This module provides deterministic
in-memory ranking that can wrap any upstream knowledge source.
"""

from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class KnowledgeItem:
    item_id: str
    text: str
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.item_id:
            raise ValueError("item_id must be non-empty")
        if not self.text:
            raise ValueError("text must be non-empty")


@dataclass(frozen=True)
class KnowledgeRetrievalResult:
    items: tuple[KnowledgeItem, ...]
    total_candidates: int
    blanket_injection_prevented: bool


def _terms(text: str) -> set[str]:
    return {token.lower() for token in re.findall(r"[A-Za-z0-9가-힣_-]+", text)}


def retrieve_relevant_knowledge(
    *,
    query: str,
    items: tuple[KnowledgeItem, ...],
    meeting_tags: tuple[str, ...] = (),
    limit: int = 5,
    min_score: int = 1,
) -> KnowledgeRetrievalResult:
    """Return only relevant knowledge items ranked by query/tag overlap."""
    if limit < 1:
        raise ValueError("limit must be >= 1")
    query_terms = _terms(query)
    tag_terms = {tag.lower() for tag in meeting_tags}
    scored: list[tuple[int, int, KnowledgeItem]] = []

    for index, item in enumerate(items):
        item_terms = _terms(item.text)
        item_tags = {tag.lower() for tag in item.tags}
        text_score = len(query_terms & item_terms)
        tag_score = 2 * len(tag_terms & item_tags)
        score = text_score + tag_score
        if score >= min_score:
            scored.append((score, -index, item))

    scored.sort(key=lambda row: (-row[0], -row[1], row[2].item_id))
    selected = tuple(row[2] for row in scored[:limit])
    return KnowledgeRetrievalResult(
        items=selected,
        total_candidates=len(items),
        blanket_injection_prevented=len(selected) < len(items),
    )
