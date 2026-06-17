"""Context compression module for meeting loop optimization.

Implements the compressed context strategy that separates raw full-text storage
from exposed loop context summaries. Mirrors the TypeScript implementations
in summarization.ts and loop-context-compression-policy.ts.

Key invariants:
- Raw turn content is retained in persistent storage (SQLite turns.content).
- Loop-visible context uses bounded summaries (visibleSummary).
- Compressed loop context carries request summary, latest verdicts,
  accepted/rejected feedback, and escalation reasons.
"""

from dataclasses import dataclass, field

from .config import CompressionConfig, default_config
from .utilities import format_list, summarize_for_thread, summarize_list


@dataclass(frozen=True)
class MeetingTurnSummary:
    """Summarized representation of a single meeting turn."""

    round: int
    role: str
    kind: str
    summary: str


@dataclass(frozen=True)
class CompressedLoopContext:
    """Input for building compressed loop context."""

    user_request_summary: str
    meeting_turns: list[MeetingTurnSummary] = field(default_factory=list)
    accepted_feedback: list[str] = field(default_factory=list)
    rejected_feedback: list[str] = field(default_factory=list)
    escalation_reasons: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CompressedLoopContextArtifact:
    """Output artifact of compressed loop context construction."""

    schema_version: str = "compressed-loop-context.v1"
    request_summary: str = ""
    latest_openclaw_summary: str = ""
    latest_hermes_summary: str = ""
    latest_hermes_verdict: str = "unknown"
    accepted_feedback: list[str] = field(default_factory=list)
    rejected_feedback: list[str] = field(default_factory=list)
    escalation_reasons: list[str] = field(default_factory=list)
    content: str = ""


@dataclass(frozen=True)
class LoopCompressionFieldPolicy:
    """Policy for a single field in loop context compression."""

    path: str
    mode: str  # "retained" | "summarized" | "dropped"
    rationale: str


@dataclass(frozen=True)
class LoopCompressionIterationBoundary:
    """Definition of an iteration boundary in the meeting loop."""

    name: str
    starts_after: str
    ends_before: str
    carried_forward: list[str]


@dataclass(frozen=True)
class LoopContextCompressionPolicy:
    """Complete policy for loop context compression."""

    schema_version: str = "loop-context-compression-policy.v1"
    deterministic_ordering: list[str] = field(default_factory=list)
    retained_fields: list[LoopCompressionFieldPolicy] = field(default_factory=list)
    summarized_fields: list[LoopCompressionFieldPolicy] = field(default_factory=list)
    dropped_fields: list[LoopCompressionFieldPolicy] = field(default_factory=list)
    iteration_boundaries: list[LoopCompressionIterationBoundary] = field(
        default_factory=list
    )
    validation_sections: list[str] = field(default_factory=list)


def summarize_meeting_turn(
    round_num: int,
    role: str,
    kind: str,
    content: str,
    max_chars: int = 1200,
) -> MeetingTurnSummary:
    """Create a summarized meeting turn from raw content.

    Args:
        round_num: The meeting round number.
        role: The agent role (e.g., 'openclaw-owner', 'hermes-reviewer').
        kind: The turn kind (e.g., 'owner_draft', 'review').
        content: The raw turn content.
        max_chars: Maximum characters for the summary.

    Returns:
        A MeetingTurnSummary with the summarized content.
    """
    return MeetingTurnSummary(
        round=round_num,
        role=role,
        kind=kind,
        summary=summarize_for_thread(content, max_chars),
    )


def _find_latest_summary(
    turns: list[MeetingTurnSummary], role: str
) -> str:
    """Find the most recent summary for a given role.

    Args:
        turns: List of meeting turn summaries.
        role: The role to search for.

    Returns:
        The latest summary string, or empty string if not found.
    """
    for turn in reversed(turns):
        if turn.role == role:
            return turn.summary
    return ""


def _infer_hermes_verdict(summary: str) -> str:
    """Infer the Hermes reviewer verdict from a review summary.

    Args:
        summary: The Hermes review summary text.

    Returns:
        One of: 'agree', 'agree_with_changes', 'disagree',
        'needs_user_decision', or 'unknown'.
    """
    import re

    normalized = summary.lower().replace(" ", "_").replace("-", "_")
    if "needs_user_decision" in normalized:
        return "needs_user_decision"
    if "agree_with_changes" in normalized:
        return "agree_with_changes"
    if re.search(r"(?:^|[^a-z])disagree(?:$|[^a-z])", normalized):
        return "disagree"
    if re.search(r"(?:^|[^a-z])agree(?:$|[^a-z])", normalized):
        return "agree"
    return "unknown"


def build_compressed_loop_context(
    input_ctx: CompressedLoopContext,
    config: CompressionConfig | None = None,
) -> CompressedLoopContextArtifact:
    """Build a compressed loop context artifact from meeting data.

    This is the core compression function that separates raw full-text storage
    from the exposed loop context. Only summaries are included; raw content
    stays in persistent storage.

    Args:
        input_ctx: The input context with request summary, meeting turns,
                   and feedback lists.
        config: Optional compression configuration. Uses defaults if None.

    Returns:
        A CompressedLoopContextArtifact with compressed content.

    Raises:
        TypeError: If user_request_summary is empty.
    """
    cfg = config if config is not None else default_config.compression

    request_summary = summarize_for_thread(
        input_ctx.user_request_summary, cfg.max_compressed_summary_chars
    )
    if not request_summary:
        raise TypeError("user_request_summary must be a non-empty string")

    latest_openclaw = _find_latest_summary(
        input_ctx.meeting_turns, "openclaw-owner"
    )
    latest_hermes = _find_latest_summary(
        input_ctx.meeting_turns, "hermes-reviewer"
    )
    verdict = _infer_hermes_verdict(latest_hermes)
    accepted_fb = summarize_list(
        input_ctx.accepted_feedback, cfg.max_feedback_summary_chars
    )
    rejected_fb = summarize_list(
        input_ctx.rejected_feedback, cfg.max_feedback_summary_chars
    )
    escalation = summarize_list(
        input_ctx.escalation_reasons, cfg.max_feedback_summary_chars
    )

    content_lines = [
        "Compressed loop context",
        f"- request_summary: {request_summary}",
        f"- latest_openclaw: {latest_openclaw or 'none'}",
        f"- latest_hermes_verdict: {verdict}",
        f"- latest_hermes: {latest_hermes or 'none'}",
        f"- accepted_feedback: {format_list(accepted_fb)}",
        f"- rejected_feedback: {format_list(rejected_fb)}",
        f"- escalation_reasons: {format_list(escalation)}",
    ]

    return CompressedLoopContextArtifact(
        schema_version="compressed-loop-context.v1",
        request_summary=request_summary,
        latest_openclaw_summary=latest_openclaw,
        latest_hermes_summary=latest_hermes,
        latest_hermes_verdict=verdict,
        accepted_feedback=accepted_fb,
        rejected_feedback=rejected_fb,
        escalation_reasons=escalation,
        content="\n".join(content_lines),
    )


def compact_prompt_context(
    messages: list[dict[str, object]],
    max_summary_chars: int = 240,
    compress_kinds: tuple[str, ...] | None = None,
    drop_kinds: tuple[str, ...] | None = None,
) -> dict[str, object]:
    """Compact a prompt context history by compressing and dropping messages.

    Args:
        messages: List of message dicts with 'id', 'kind', 'content', and
                  optional 'round' and 'role' keys.
        max_summary_chars: Maximum characters for compressed summaries.
        compress_kinds: Message kinds to compress. Defaults to ('meeting_turn',).
        drop_kinds: Message kinds to drop. Defaults to
                    ('raw_prompt_echo', 'scratchpad').

    Returns:
        Dict with compaction results including compacted messages,
        removed IDs, original/compacted counts, and character counts.
    """
    if compress_kinds is None:
        compress_kinds = ("meeting_turn",)
    if drop_kinds is None:
        drop_kinds = ("raw_prompt_echo", "scratchpad")

    compress_set = set(compress_kinds)
    drop_set = set(drop_kinds)
    removed_ids: list[str] = []
    compressed_ids: list[str] = []
    compacted: list[dict[str, object]] = []
    original_char_count = 0
    compacted_char_count = 0

    for msg in messages:
        kind = str(msg.get("kind", ""))
        msg_id = str(msg.get("id", ""))
        content = str(msg.get("content", "")).strip()
        original_char_count += len(content)

        if kind in drop_set:
            removed_ids.append(msg_id)
            continue

        should_compress = kind in compress_set
        compacted_content = (
            summarize_for_thread(content, max_summary_chars)
            if should_compress
            else content
        )

        if should_compress and compacted_content != content:
            compressed_ids.append(msg_id)

        compacted_char_count += len(compacted_content)
        entry: dict[str, object] = {
            "id": msg_id,
            "kind": kind,
            "disposition": "compressed" if should_compress else "retained",
            "content": compacted_content,
            "original_chars": len(content),
            "compacted_chars": len(compacted_content),
        }
        if "round" in msg:
            entry["round"] = msg["round"]
        if "role" in msg:
            entry["role"] = msg["role"]
        compacted.append(entry)

    return {
        "schema_version": "prompt-context-compaction.v1",
        "messages": compacted,
        "removed_message_ids": removed_ids,
        "compressed_message_ids": compressed_ids,
        "original_message_count": len(messages),
        "compacted_message_count": len(compacted),
        "original_char_count": original_char_count,
        "compacted_char_count": compacted_char_count,
    }


def build_loop_context_compression_policy() -> LoopContextCompressionPolicy:
    """Build the default loop context compression policy.

    Returns:
        A LoopContextCompressionPolicy with retained, summarized, and
        dropped field definitions plus iteration boundaries.
    """
    return LoopContextCompressionPolicy(
        schema_version="loop-context-compression-policy.v1",
        retained_fields=[
            LoopCompressionFieldPolicy(
                path="tasks.user_request",
                mode="retained",
                rationale=(
                    "Keep the original user request as the replay and "
                    "audit source of truth."
                ),
            ),
            LoopCompressionFieldPolicy(
                path="turns.content",
                mode="retained",
                rationale=(
                    "Keep complete OpenClaw, Hermes, final synthesis, and "
                    "escalation text outside normal loop prompts."
                ),
            ),
            LoopCompressionFieldPolicy(
                path="decisions.reasons",
                mode="retained",
                rationale=(
                    "Keep exact escalation and convergence reasons for "
                    "deterministic failure analysis."
                ),
            ),
        ],
        summarized_fields=[
            LoopCompressionFieldPolicy(
                path="tasks.user_request_summary",
                mode="summarized",
                rationale=(
                    "Expose a bounded summary of the request to each "
                    "meeting iteration."
                ),
            ),
            LoopCompressionFieldPolicy(
                path="turns.visibleSummary",
                mode="summarized",
                rationale=(
                    "Expose role, kind, round, and bounded summary "
                    "instead of raw turn content."
                ),
            ),
            LoopCompressionFieldPolicy(
                path="compressedLoopContext.acceptedFeedback",
                mode="summarized",
                rationale=(
                    "Carry only actionable Hermes feedback that OpenClaw "
                    "accepted."
                ),
            ),
            LoopCompressionFieldPolicy(
                path="compressedLoopContext.rejectedFeedback",
                mode="summarized",
                rationale=(
                    "Carry only rejected feedback labels and rationale "
                    "summaries to prevent repeated debate."
                ),
            ),
            LoopCompressionFieldPolicy(
                path="compressedLoopContext.escalationReasons",
                mode="summarized",
                rationale=(
                    "Carry concise blockers when convergence fails or "
                    "user input is required."
                ),
            ),
        ],
        dropped_fields=[
            LoopCompressionFieldPolicy(
                path="turns.content.rawPromptEcho",
                mode="dropped",
                rationale=(
                    "Prompt echoes are redundant after raw turn storage "
                    "and visible summaries exist."
                ),
            ),
            LoopCompressionFieldPolicy(
                path="turns.content.intermediateScratchpad",
                mode="dropped",
                rationale=(
                    "Private scratchpad-style text must not be replayed "
                    "into meeting context."
                ),
            ),
            LoopCompressionFieldPolicy(
                path="duplicatePriorRoundFullText",
                mode="dropped",
                rationale=(
                    "Older full-text rounds are represented by summaries "
                    "and retained only in raw storage."
                ),
            ),
        ],
        iteration_boundaries=[
            LoopCompressionIterationBoundary(
                name="request_analysis_to_openclaw",
                starts_after="task_breakdown_and_role_routing",
                ends_before="openclaw_owner_draft",
                carried_forward=[
                    "tasks.user_request_summary",
                    "role_routes",
                    "active_task_ids",
                ],
            ),
            LoopCompressionIterationBoundary(
                name="openclaw_to_hermes",
                starts_after="openclaw_owner_draft",
                ends_before="hermes_review",
                carried_forward=[
                    "tasks.user_request_summary",
                    "latest_openclaw_summary",
                    "accepted_constraints",
                ],
            ),
            LoopCompressionIterationBoundary(
                name="hermes_to_next_openclaw_or_final",
                starts_after="hermes_review",
                ends_before="next_openclaw_draft_or_final_synthesis",
                carried_forward=[
                    "tasks.user_request_summary",
                    "latest_openclaw_summary",
                    "latest_hermes_verdict",
                    "acceptedFeedback",
                    "rejectedFeedback",
                    "escalationReasons",
                ],
            ),
        ],
        deterministic_ordering=[
            "schemaVersion",
            "retainedFields.path",
            "summarizedFields.path",
            "droppedFields.path",
            "iterationBoundaries.name",
            "validationSections",
        ],
        validation_sections=[
            "Retained Fields",
            "Summarized Fields",
            "Dropped Fields",
            "Iteration Boundaries",
            "Deterministic Ordering",
        ],
    )
