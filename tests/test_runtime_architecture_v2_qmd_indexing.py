from __future__ import annotations

import json
import threading

from src.runtime_architecture_v2 import qmd_indexing
from src.runtime_architecture_v2.qmd_indexing import (
    QmdIndexScheduler,
    QmdReconcileResult,
)
from src.runtime_architecture_v2.qmd_search import QmdCommandResult


class FakeQmdClient:
    def __init__(self, *, update_ok: bool = True, embed_ok: bool = True) -> None:
        self.calls: list[str] = []
        self.update_result = QmdCommandResult(
            ok=update_ok, error="" if update_ok else "update_failed"
        )
        self.embed_result = QmdCommandResult(
            ok=embed_ok, error="" if embed_ok else "embed_failed"
        )

    def update(self) -> QmdCommandResult:
        self.calls.append("update")
        return self.update_result

    def embed(self) -> QmdCommandResult:
        self.calls.append("embed")
        return self.embed_result


def test_mark_dirty_persists_state_under_runtime_qmd(tmp_path):
    scheduler = QmdIndexScheduler(runtime_root=tmp_path, client=FakeQmdClient())

    scheduler.mark_dirty()

    dirty_path = tmp_path / "runtime" / "qmd" / "dirty.json"
    assert json.loads(dirty_path.read_text(encoding="utf-8")) == {"dirty": True}
    recreated = QmdIndexScheduler(runtime_root=tmp_path, client=FakeQmdClient())
    assert recreated.dirty is True


def test_reconcile_updates_embeds_and_clears_dirty_marker_after_both_succeed(tmp_path):
    client = FakeQmdClient()
    scheduler = QmdIndexScheduler(runtime_root=tmp_path, client=client)
    scheduler.mark_dirty()

    result = scheduler.reconcile()

    assert result == QmdReconcileResult(ok=True, updated=True, embedded=True)
    assert client.calls == ["update", "embed"]
    assert scheduler.dirty is False


def test_reconcile_skips_embed_and_keeps_dirty_marker_when_update_fails(tmp_path):
    client = FakeQmdClient(update_ok=False)
    scheduler = QmdIndexScheduler(runtime_root=tmp_path, client=client)
    scheduler.mark_dirty()

    result = scheduler.reconcile()

    assert result == QmdReconcileResult(ok=False, error="update_failed")
    assert client.calls == ["update"]
    assert scheduler.dirty is True


def test_reconcile_keeps_dirty_marker_when_embed_fails(tmp_path):
    client = FakeQmdClient(embed_ok=False)
    scheduler = QmdIndexScheduler(runtime_root=tmp_path, client=client)
    scheduler.mark_dirty()

    result = scheduler.reconcile()

    assert result == QmdReconcileResult(
        ok=False, updated=True, error="embed_failed"
    )
    assert client.calls == ["update", "embed"]
    assert scheduler.dirty is True


def test_refresh_for_search_runs_only_update_and_preserves_dirty_marker(tmp_path):
    client = FakeQmdClient()
    scheduler = QmdIndexScheduler(runtime_root=tmp_path, client=client)
    scheduler.mark_dirty()

    result = scheduler.refresh_for_search()

    assert result == QmdCommandResult(ok=True)
    assert client.calls == ["update"]
    assert scheduler.dirty is True


def test_two_scheduler_instances_serialize_index_operations(tmp_path):
    update_started = threading.Event()
    allow_update = threading.Event()

    class BlockingClient(FakeQmdClient):
        def update(self) -> QmdCommandResult:
            self.calls.append("update")
            update_started.set()
            assert allow_update.wait(timeout=1)
            return self.update_result

    first_client = BlockingClient()
    second_client = FakeQmdClient()
    first = QmdIndexScheduler(runtime_root=tmp_path, client=first_client)
    second = QmdIndexScheduler(runtime_root=tmp_path, client=second_client)
    first.mark_dirty()
    first_thread = threading.Thread(target=first.reconcile)
    second_thread = threading.Thread(target=second.refresh_for_search)

    first_thread.start()
    assert update_started.wait(timeout=1)
    second_thread.start()
    second_thread.join(timeout=0.05)

    assert second_thread.is_alive()
    assert second_client.calls == []
    allow_update.set()
    first_thread.join(timeout=1)
    second_thread.join(timeout=1)

    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert first_client.calls == ["update", "embed"]
    assert second_client.calls == ["update"]


def test_schedule_coalesces_calls_into_one_daemon_worker(tmp_path, monkeypatch):
    debounce_started = threading.Event()
    allow_reconcile = threading.Event()

    def debounce(delay: float) -> None:
        assert delay == 5
        debounce_started.set()
        assert allow_reconcile.wait(timeout=1)

    monkeypatch.setattr(qmd_indexing.time, "sleep", debounce)
    scheduler = QmdIndexScheduler(runtime_root=tmp_path, client=FakeQmdClient())
    scheduler.mark_dirty()

    assert scheduler.schedule() is True
    assert debounce_started.wait(timeout=1)
    worker = scheduler._worker
    assert worker is not None
    assert worker.daemon is True
    assert scheduler.schedule() is False

    allow_reconcile.set()
    worker.join(timeout=1)
    assert not worker.is_alive()
    assert scheduler.client.calls == ["update", "embed"]
    assert scheduler.dirty is False
