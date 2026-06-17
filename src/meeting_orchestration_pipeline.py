"""Integration wiring for the meeting orchestration pipeline.

This module connects the pure AC modules into one deterministic coordinator
entry point without making live Discord, Hermes, opencode-go, or OpenClaw
calls.  Runtime adapters can call this boundary and then execute the returned
queue dispatches and Discord delivery plan.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.append_only_log import AppendOnlyDecisionLog, DecisionEvent
from src.discord_delivery import DiscordDeliveryPlan, plan_discord_delivery
from src.knowledge_retrieval_service import (
    KnowledgeItem,
    KnowledgeRetrievalResult,
    retrieve_relevant_knowledge,
)
from src.meeting_creation_dispatcher import (
    DispatchResult,
    OrchestratorCallable,
    dispatch_meeting,
)
from src.meeting_intent_parser import MeetingIntent, NoMeetingIntent, parse_meeting_intent
from src.meeting_trigger import MeetingConfig
from src.priority_queue import MeetingQueueItem, PriorityMeetingQueue


@dataclass(frozen=True)
class MeetingPipelineRequest:
    """External request normalized for the meeting coordinator boundary."""

    text: str
    user_id: str
    channel_id: str
    thread_id: str
    guild_id: str = ""
    result_channel_id: str = ""
    created_at: int | float = 0
    force_meeting_intent: bool = False


@dataclass(frozen=True)
class MeetingPipelineResult:
    """Result of parsing, creating, queueing, logging, and planning delivery."""

    success: bool
    error: str = ""
    intent: MeetingIntent | None = None
    dispatch: DispatchResult | None = None
    queued_item: MeetingQueueItem | None = None
    dispatched_items: tuple[MeetingQueueItem, ...] = ()
    knowledge: KnowledgeRetrievalResult | None = None
    decision_event: DecisionEvent | None = None
    delivery_plan: DiscordDeliveryPlan | None = None


def process_meeting_request(
    request: MeetingPipelineRequest,
    *,
    queue: PriorityMeetingQueue,
    knowledge_items: tuple[KnowledgeItem, ...] = (),
    decision_log: AppendOnlyDecisionLog | None = None,
    meetings_root: str | None = None,
    config: MeetingConfig | None = None,
    orchestrator: OrchestratorCallable | None = None,
) -> MeetingPipelineResult:
    """Process one meeting request through the integrated pure pipeline.

    Steps:
    1. Parse natural-language text into a ``MeetingIntent``.
    2. Dispatch the intent to the meeting creator/orchestrator.
    3. Enqueue the created meeting using P0-P3 priority ordering.
    4. Drain available queue slots under the concurrency cap.
    5. Retrieve bounded relevant knowledge for the meeting topic/tags.
    6. Append an immutable creation event when a decision log is supplied.
    7. Return a Discord delivery plan for adapter-side posting.
    """

    parsed = parse_meeting_intent(
        request.text,
        force_meeting=request.force_meeting_intent,
    )
    if isinstance(parsed, NoMeetingIntent) or not parsed.is_meeting:
        return MeetingPipelineResult(success=False, error="no meeting intent detected")

    intent = parsed
    dispatch = dispatch_meeting(
        intent,
        user_id=request.user_id,
        channel_id=request.channel_id,
        thread_id=request.thread_id,
        guild_id=request.guild_id,
        meetings_root=meetings_root,
        config=config,
        orchestrator=orchestrator,
    )
    if not dispatch.success or dispatch.context is None:
        return MeetingPipelineResult(
            success=False,
            error=dispatch.error or "meeting dispatch failed",
            intent=intent,
            dispatch=dispatch,
        )

    manifest = dispatch.context.manifest
    queued_item = MeetingQueueItem(
        meeting_id=dispatch.context.meeting_id,
        priority=manifest.priority.upper(),
        created_at=request.created_at,
        payload={
            "agenda": manifest.agenda,
            "thread_id": manifest.thread_id,
            "channel_id": manifest.channel_id,
            "guild_id": manifest.guild_id,
        },
    )
    queue.enqueue(queued_item)
    dispatched_items = queue.drain_ready_slots()

    knowledge = retrieve_relevant_knowledge(
        query=intent.topic,
        items=knowledge_items,
        meeting_tags=tuple(intent.teams),
        limit=5,
    )

    decision_event: DecisionEvent | None = None
    if decision_log is not None:
        decision_event = DecisionEvent(
            event_id=f"{dispatch.context.meeting_id}:created",
            decision_id=dispatch.context.meeting_id,
            content=f"Meeting created: {intent.topic}",
            metadata={
                "state": manifest.state,
                "priority": manifest.priority,
                "thread_id": manifest.thread_id,
            },
        )
        decision_log.append(decision_event)

    result_channel_id = request.result_channel_id or request.channel_id
    delivery_plan = plan_discord_delivery(
        meeting_id=dispatch.context.meeting_id,
        original_thread_id=request.thread_id,
        response=f"회의가 접수되었습니다: {intent.topic}",
        summary=f"[{manifest.priority.upper()}] {intent.topic}",
        result_channel_id=result_channel_id,
    )

    return MeetingPipelineResult(
        success=True,
        intent=intent,
        dispatch=dispatch,
        queued_item=queued_item,
        dispatched_items=dispatched_items,
        knowledge=knowledge,
        decision_event=decision_event,
        delivery_plan=delivery_plan,
    )


__all__ = [
    "MeetingPipelineRequest",
    "MeetingPipelineResult",
    "process_meeting_request",
]
