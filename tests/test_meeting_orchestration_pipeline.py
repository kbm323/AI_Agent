from __future__ import annotations

from src.append_only_log import AppendOnlyDecisionLog
from src.knowledge_retrieval_service import KnowledgeItem
from src.meeting_orchestration_pipeline import (
    MeetingPipelineRequest,
    process_meeting_request,
)
from src.priority_queue import PriorityMeetingQueue


def test_process_meeting_request_wires_creation_queue_knowledge_log_and_delivery(tmp_path):
    queue = PriorityMeetingQueue(max_concurrent=2)
    decision_log = AppendOnlyDecisionLog()
    knowledge = (
        KnowledgeItem("k1", "API latency and performance tuning notes", tags=("technical",)),
        KnowledgeItem("k2", "marketing launch calendar", tags=("marketing",)),
        KnowledgeItem("k3", "technical API incident playbook", tags=("technical",)),
    )

    result = process_meeting_request(
        MeetingPipelineRequest(
            text="긴급 기술 회의 열어줘. API latency 개선 논의",
            user_id="user-1",
            channel_id="channel-1",
            thread_id="thread-1",
            guild_id="guild-1",
            result_channel_id="results-1",
            created_at=10,
        ),
        queue=queue,
        knowledge_items=knowledge,
        decision_log=decision_log,
        meetings_root=str(tmp_path),
    )

    assert result.success
    assert result.dispatch is not None and result.dispatch.success
    assert result.queued_item is not None
    assert result.queued_item.meeting_id == result.dispatch.context.meeting_id
    assert result.queued_item.priority in {"P0", "P1", "P2", "P3"}
    assert [item.meeting_id for item in result.dispatched_items] == [result.queued_item.meeting_id]
    assert queue.running_ids == (result.queued_item.meeting_id,)
    assert result.knowledge is not None
    assert [item.item_id for item in result.knowledge.items] == ["k1", "k3"]
    assert result.decision_event is not None
    assert decision_log.get(result.queued_item.meeting_id) == result.decision_event
    assert result.delivery_plan is not None
    assert result.delivery_plan.primary.thread_id == "thread-1"
    assert result.delivery_plan.cross_post.channel_id == "results-1"


def test_process_meeting_request_respects_queue_concurrency_cap(tmp_path):
    queue = PriorityMeetingQueue(max_concurrent=1)

    first = process_meeting_request(
        MeetingPipelineRequest(
            text="회의 열어줘. 첫 번째 주제",
            user_id="user-1",
            channel_id="channel-1",
            thread_id="thread-1",
            result_channel_id="results-1",
            created_at=1,
        ),
        queue=queue,
        meetings_root=str(tmp_path),
    )
    second = process_meeting_request(
        MeetingPipelineRequest(
            text="회의 열어줘. 두 번째 주제",
            user_id="user-2",
            channel_id="channel-1",
            thread_id="thread-2",
            result_channel_id="results-1",
            created_at=2,
        ),
        queue=queue,
        meetings_root=str(tmp_path),
    )

    assert first.success
    assert second.success
    assert len(first.dispatched_items) == 1
    assert second.dispatched_items == ()
    assert second.queued_item is not None
    assert queue.pending_ids == (second.queued_item.meeting_id,)


def test_process_meeting_request_rejects_non_meeting_text_without_side_effects(tmp_path):
    queue = PriorityMeetingQueue(max_concurrent=2)
    decision_log = AppendOnlyDecisionLog()

    result = process_meeting_request(
        MeetingPipelineRequest(
            text="그냥 상태만 확인해줘",
            user_id="user-1",
            channel_id="channel-1",
            thread_id="thread-1",
            result_channel_id="results-1",
        ),
        queue=queue,
        decision_log=decision_log,
        meetings_root=str(tmp_path),
    )

    assert not result.success
    assert result.error == "no meeting intent detected"
    assert result.queued_item is None
    assert queue.pending_ids == ()
    assert queue.running_ids == ()
    assert decision_log.events == ()
