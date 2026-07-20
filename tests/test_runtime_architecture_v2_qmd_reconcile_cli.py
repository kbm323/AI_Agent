from __future__ import annotations

import json

from scripts.run_qmd_reconcile import main
from src.runtime_architecture_v2.qmd_search import QmdCommandResult


class FakeQmdClient:
    def __init__(self, *, update_ok: bool = True, embed_ok: bool = True):
        self.update_ok = update_ok
        self.embed_ok = embed_ok
        self.calls: list[str] = []

    def update(self) -> QmdCommandResult:
        self.calls.append("update")
        return QmdCommandResult(
            ok=self.update_ok,
            error="command_failed" if not self.update_ok else "",
        )

    def embed(self) -> QmdCommandResult:
        self.calls.append("embed")
        return QmdCommandResult(
            ok=self.embed_ok,
            error="command_failed" if not self.embed_ok else "",
        )


def test_reconcile_cli_clears_dirty_state_only_after_update_and_embed(
    tmp_path, capsys
):
    dirty = tmp_path / "runtime" / "qmd" / "dirty.json"
    dirty.parent.mkdir(parents=True)
    dirty.write_text('{"dirty": true}\n', encoding="utf-8")
    client = FakeQmdClient()

    exit_code = main(["--root", str(tmp_path)], client=client)

    assert exit_code == 0
    assert client.calls == ["update", "embed"]
    assert not dirty.exists()
    assert json.loads(capsys.readouterr().out) == {
        "embedded": True,
        "ok": True,
        "updated": True,
    }


def test_reconcile_cli_preserves_dirty_state_on_qmd_failure(tmp_path, capsys):
    dirty = tmp_path / "runtime" / "qmd" / "dirty.json"
    dirty.parent.mkdir(parents=True)
    dirty.write_text('{"dirty": true}\n', encoding="utf-8")
    client = FakeQmdClient(update_ok=False)

    exit_code = main(["--root", str(tmp_path)], client=client)

    assert exit_code == 1
    assert client.calls == ["update"]
    assert dirty.exists()
    assert json.loads(capsys.readouterr().out) == {
        "embedded": False,
        "error": "command_failed",
        "ok": False,
        "updated": False,
    }


def test_reconcile_cli_rejects_missing_root_without_exposing_path(tmp_path, capsys):
    missing = tmp_path / "private" / "missing-root"

    exit_code = main(["--root", str(missing)], client=FakeQmdClient())

    assert exit_code == 2
    output = capsys.readouterr().out
    assert str(missing) not in output
    assert json.loads(output) == {"error": "invalid_root", "ok": False}
