from __future__ import annotations

import subprocess

import pytest

from src.runtime_architecture_v2 import qmd_search
from src.runtime_architecture_v2.qmd_search import (
    QmdClient,
    QmdMatch,
    QmdRawResult,
)


class FakeRunner:
    def __init__(self, stdout: str = "", exit_code: int = 0) -> None:
        self.calls: list[list[str]] = []
        self.result = QmdRawResult(exit_code=exit_code, stdout=stdout, stderr="ignored")

    def __call__(self, argv: list[str], timeout_seconds: float) -> QmdRawResult:
        self.calls.append(argv)
        assert timeout_seconds == 120
        return self.result


class SequenceRunner:
    def __init__(self, results: list[QmdRawResult]) -> None:
        self.calls: list[list[str]] = []
        self.results = results

    def __call__(self, argv: list[str], timeout_seconds: float) -> QmdRawResult:
        self.calls.append(argv)
        return self.results.pop(0)


def succeeded(stdout: str) -> QmdRawResult:
    return QmdRawResult(exit_code=0, stdout=stdout, stderr="")


def failed() -> QmdRawResult:
    return QmdRawResult(exit_code=1, stdout="", stderr="model unavailable")


def test_query_uses_argument_vector_and_parses_vault_relative_matches():
    runner = FakeRunner('[{"file":"wiki/a.md","score":0.9,"snippet":"alpha"}]')

    result = QmdClient(runner=runner).query("Korean question", limit=3)

    assert runner.calls == [
        ["qmd", "query", "Korean question", "--json", "-c", "obsidian", "-n", "3"]
    ]
    assert result.matches == (
        QmdMatch(path="wiki/a.md", snippet="alpha", score=0.9),
    )


def test_query_falls_back_to_bm25_when_hybrid_query_fails():
    runner = SequenceRunner(
        [failed(), succeeded('[{"file":"wiki/a.md","score":0.9,"snippet":"alpha"}]')]
    )

    result = QmdClient(runner=runner).query("query")

    assert runner.calls[1][:3] == ["qmd", "search", "query"]
    assert result.fallback == "bm25"
    assert result.ok is True


def test_query_rejects_blank_input_and_unsafe_result_paths():
    with pytest.raises(ValueError, match="blank_query"):
        QmdClient(runner=FakeRunner()).query("  ")

    absolute = FakeRunner('[{"file":"C:/vault/wiki/a.md","score":0.9,"snippet":"a"}]')
    traversal = FakeRunner('[{"file":"wiki/../secrets.md","score":0.9,"snippet":"a"}]')
    other_collection = FakeRunner(
        '[{"file":"qmd://other/wiki/a.md","score":0.9,"snippet":"a"}]'
    )
    uri_absolute = FakeRunner(
        '[{"file":"qmd://obsidian//etc/passwd","score":0.9,"snippet":"a"}]'
    )

    assert QmdClient(runner=absolute).query("x").error == "unsafe_result"
    assert QmdClient(runner=traversal).query("x").error == "unsafe_result"
    assert QmdClient(runner=other_collection).query("x").error == "unsafe_result"
    assert QmdClient(runner=uri_absolute).query("x").error == "unsafe_result"


def test_query_normalizes_obsidian_uri_and_caps_documented_results_envelope():
    # QMD's documented JSON envelope is supported narrowly through its results list.
    runner = FakeRunner(
        '{"results":[{"file":"qmd://obsidian/wiki/a.md","score":1,"snippet":"a"},'
        '{"file":"wiki/b.md","score":0.5,"snippet":"b"}]}'
    )

    result = QmdClient(runner=runner).query("query", limit=1)

    assert result.matches == (QmdMatch(path="wiki/a.md", snippet="a", score=1.0),)


def test_query_returns_sanitized_malformed_result_error():
    result = QmdClient(runner=FakeRunner("not json")).query("query")

    assert result.error == "malformed_result"


def test_default_runner_uses_non_shell_argument_array_and_maps_os_errors(monkeypatch):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(qmd_search.subprocess, "run", fake_run)

    result = QmdClient().update()

    assert result.ok is True
    assert calls == [
        (
            ["qmd", "update", "-c", "obsidian"],
            {
                "capture_output": True,
                "text": True,
                "timeout": 120,
                "shell": False,
                "check": False,
            },
        )
    ]

    def missing(*_args, **_kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(qmd_search.subprocess, "run", missing)
    assert QmdClient().embed().error == "executable_not_found"


def test_update_and_embed_map_timeout_and_command_failures():
    def timeout_runner(_argv, _timeout_seconds):
        raise subprocess.TimeoutExpired("qmd", 120)

    assert QmdClient(runner=timeout_runner).update().error == "timeout"

    failed_runner = SequenceRunner([failed(), failed()])
    assert QmdClient(runner=failed_runner).update().error == "command_failed"
    assert QmdClient(runner=failed_runner).embed().error == "command_failed"
