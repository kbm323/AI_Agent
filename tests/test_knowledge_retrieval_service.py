"""Tests for bounded dynamic knowledge retrieval (AC20)."""

from __future__ import annotations

from src.knowledge_retrieval_service import KnowledgeItem, retrieve_relevant_knowledge


def test_retrieves_only_relevant_items_not_blanket_injection() -> None:
    items = (
        KnowledgeItem(item_id="k1", text="Discord thread delivery and cross-post rules", tags=("discord", "thread")),
        KnowledgeItem(item_id="k2", text="OpenClaw high risk HITL approval", tags=("openclaw", "risk")),
        KnowledgeItem(item_id="k3", text="Unrelated music marketing plan", tags=("marketing",)),
    )

    result = retrieve_relevant_knowledge(
        query="How should Discord thread responses be delivered?",
        items=items,
        meeting_tags=("discord",),
        limit=2,
    )

    assert [item.item_id for item in result.items] == ["k1"]
    assert result.blanket_injection_prevented is True
    assert result.total_candidates == 3


def test_limit_caps_retrieved_items() -> None:
    items = tuple(
        KnowledgeItem(item_id=f"k{i}", text="priority queue discord meeting", tags=("discord",))
        for i in range(5)
    )

    result = retrieve_relevant_knowledge(
        query="discord meeting priority",
        items=items,
        meeting_tags=("discord",),
        limit=2,
    )

    assert len(result.items) == 2
