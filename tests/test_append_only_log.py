"""Tests for append-only meeting/decision logs (AC21)."""

from __future__ import annotations

from src.append_only_log import AppendOnlyDecisionLog, DecisionEvent


def test_log_appends_superseding_decision_without_mutating_original() -> None:
    log = AppendOnlyDecisionLog()
    first = DecisionEvent(event_id="e1", decision_id="d1", content="Use GLM", metadata={})
    second = DecisionEvent(
        event_id="e2",
        decision_id="d2",
        content="Use Codex for legal",
        metadata={"supersedes": "d1"},
    )

    log.append(first)
    log.append(second)

    assert [event.decision_id for event in log.events] == ["d1", "d2"]
    assert log.get("d1").content == "Use GLM"
    assert log.current_decision_for("d1").decision_id == "d2"


def test_duplicate_event_id_rejected_to_preserve_append_only_audit() -> None:
    log = AppendOnlyDecisionLog()
    log.append(DecisionEvent(event_id="e1", decision_id="d1", content="A", metadata={}))

    try:
        log.append(DecisionEvent(event_id="e1", decision_id="d2", content="B", metadata={}))
    except ValueError as exc:
        assert "event_id" in str(exc)
    else:
        raise AssertionError("duplicate event_id should fail")
