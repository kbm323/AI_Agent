from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from src.runtime_architecture_v2 import llmwiki_sources as module
from src.runtime_architecture_v2.llmwiki_sources import (
    AbxDlRunResult,
    SourceError,
    SourceRetriever,
    extract_single_url,
    normalize_source_url,
)

SAFE_URL = "https://public.example/article?topic=llm&lang=en"
YOUTUBE_URL = "https://www.youtube.com/watch?v=video123"
INSTAGRAM_URL = "https://www.instagram.com/p/post123/"
THREADS_URL = "https://www.threads.net/@author/post/post123"
PUBLIC_IP = "93.184.216.34"


class FakeResolver:
    def __init__(self, addresses: dict[str, tuple[str, ...]] | None = None):
        self.addresses = addresses or {
            "public.example": (PUBLIC_IP,),
            "www.youtube.com": ("142.250.72.46",),
            "www.instagram.com": ("157.240.241.174",),
            "www.threads.net": ("157.240.241.17",),
        }
        self.calls: list[tuple[str, int]] = []

    def __call__(self, host: str, port: int) -> tuple[str, ...]:
        self.calls.append((host, port))
        return self.addresses[host]


@dataclass
class FakeAbxDlRunner:
    files: dict[str, str | bytes] = field(default_factory=dict)
    returncode: int = 0
    output_root_override: Path | None = None
    calls: int = 0
    argv: list[str] = field(default_factory=list)
    cwd: Path | None = None
    timeout: float | None = None
    max_bytes: int | None = None
    max_files: int | None = None

    def __call__(
        self,
        argv: list[str],
        *,
        cwd: str,
        timeout: float,
        max_bytes: int,
        max_files: int,
    ) -> AbxDlRunResult:
        self.calls += 1
        self.argv = list(argv)
        self.cwd = Path(cwd)
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.max_files = max_files
        self.cwd.mkdir(parents=True, exist_ok=True)
        index_path = self.cwd / "index.jsonl"
        index_path.write_text(
            json.dumps({"type": "Snapshot", "url": argv[-1]}) + "\n",
            encoding="utf-8",
        )
        for relative, content in self.files.items():
            path = self.cwd / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, bytes):
                path.write_bytes(content)
            else:
                path.write_text(content, encoding="utf-8")
        return AbxDlRunResult(
            returncode=self.returncode,
            output_root=self.output_root_override or self.cwd,
        )


class BlockingResolver:
    def __call__(self, host: str, port: int) -> tuple[str, ...]:
        del host, port
        time.sleep(0.2)
        return (PUBLIC_IP,)


class FakeProcess:
    def __init__(self, polls: list[int | None] | None = None):
        self.polls = list(polls or [0])
        self.returncode: int | None = None
        self.pid = 4321
        self.killed = False

    def poll(self):
        value = self.polls.pop(0) if self.polls else self.returncode
        if value is not None:
            self.returncode = value
        return value

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self, timeout=None):
        del timeout
        return self.returncode


@pytest.mark.parametrize("text", ["no url", "https://a.example https://b.example"])
def test_extract_single_url_rejects_missing_or_ambiguous_input(text):
    with pytest.raises(SourceError, match="^invalid_url$"):
        extract_single_url(text)


def test_extract_single_url_trims_sentence_punctuation():
    assert extract_single_url("Read https://Example.com/path?x=1, please.") == (
        "https://Example.com/path?x=1"
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/%zz",
        "https://example..com/",
        "https://example.com:0/",
        "https://example.com:/",
        "https://bad_host.example/",
        "https://user:password@example.com/",
        "file:///etc/passwd",
    ],
)
def test_normalize_source_url_rejects_malformed_or_unsupported_urls(url):
    with pytest.raises(SourceError, match="^invalid_url$") as error:
        normalize_source_url(url)

    assert error.value.__cause__ is None


def test_normalize_source_url_preserves_semantic_query_order():
    normalized = normalize_source_url(
        "HTTPS://Example.COM:443/a%20path?b=2&a=1&a=0#section"
    )

    assert normalized == "https://example.com/a%20path?b=2&a=1&a=0"


def test_private_target_is_rejected_before_abxdl_runs():
    runner = FakeAbxDlRunner({"readability/content.txt": "not reached"})

    with pytest.raises(SourceError, match="^unsafe_target$"):
        SourceRetriever(abxdl_runner=runner, resolver=FakeResolver()).retrieve(
            "http://127.0.0.1/private"
        )

    assert runner.calls == 0


def test_any_private_resolved_address_is_rejected_before_abxdl_runs():
    runner = FakeAbxDlRunner({"readability/content.txt": "not reached"})
    resolver = FakeResolver({"public.example": (PUBLIC_IP, "10.0.0.8")})

    with pytest.raises(SourceError, match="^unsafe_target$"):
        SourceRetriever(abxdl_runner=runner, resolver=resolver).retrieve(SAFE_URL)

    assert runner.calls == 0


def test_dns_wait_is_bounded(monkeypatch):
    monkeypatch.setattr(module, "RETRIEVAL_TIMEOUT_SECONDS", 0.01)
    runner = FakeAbxDlRunner({"readability/content.txt": "not reached"})
    started = time.monotonic()

    with pytest.raises(SourceError, match="^timeout$"):
        SourceRetriever(abxdl_runner=runner, resolver=BlockingResolver()).retrieve(
            SAFE_URL
        )

    assert time.monotonic() - started < 0.1
    assert runner.calls == 0


@pytest.mark.parametrize(
    "url",
    [SAFE_URL, YOUTUBE_URL, INSTAGRAM_URL, THREADS_URL],
)
def test_every_source_uses_the_same_abxdl_runner_once(url):
    runner = FakeAbxDlRunner({"readability/content.txt": "usable evidence"})

    source = SourceRetriever(
        abxdl_runner=runner,
        resolver=FakeResolver(),
    ).retrieve(url)

    assert runner.calls == 1
    assert runner.argv[0] == "abx-dl"
    assert "--no-install" in runner.argv
    assert any(argument.startswith("--dir=") for argument in runner.argv)
    assert runner.argv[-1] == normalize_source_url(url)
    assert source.content == "usable evidence"
    assert source.metadata["acquisition_adapter"] == "abx-dl"
    assert runner.cwd is not None
    assert not runner.cwd.exists()


def test_artifact_priority_is_transcript_then_clean_text_then_metadata():
    runner = FakeAbxDlRunner(
        {
            "ytdlp/video.ko.vtt": (
                "WEBVTT\nKind: captions\nLanguage: ko\n\n"
                "00:00.000 --> 00:01.000\nspoken evidence\n"
            ),
            "readability/content.txt": "article evidence",
            "gallerydl/info.json": json.dumps({"description": "social evidence"}),
            "dom/output.html": "<main>rendered evidence</main>",
            "title/title.txt": "Captured title",
        }
    )

    source = SourceRetriever(
        abxdl_runner=runner,
        resolver=FakeResolver(),
    ).retrieve(YOUTUBE_URL)

    assert source.content == "spoken evidence"
    assert source.title == "Captured title"
    assert source.source_type == "video"
    assert source.metadata["extractor"] == "ytdlp"
    assert source.metadata["artifact_path"] == "ytdlp/video.ko.vtt"


@pytest.mark.parametrize(
    ("url", "files", "expected_content", "expected_type"),
    [
        (
            SAFE_URL,
            {"trafilatura/content.md": "# Article\n\nUseful article body"},
            "# Article\n\nUseful article body",
            "article",
        ),
        (
            INSTAGRAM_URL,
            {
                "gallerydl/post.json": json.dumps(
                    {"title": "Post title", "caption": "Instagram insight"}
                )
            },
            "Instagram insight",
            "social",
        ),
        (
            THREADS_URL,
            {"dom/output.html": "<main>Threads public post</main>"},
            "Threads public post",
            "web",
        ),
    ],
)
def test_selects_textual_evidence_without_domain_specific_dispatch(
    url, files, expected_content, expected_type
):
    source = SourceRetriever(
        abxdl_runner=FakeAbxDlRunner(files),
        resolver=FakeResolver(),
    ).retrieve(url)

    assert source.content == expected_content
    assert source.source_type == expected_type


def test_captured_json_cannot_override_internal_provenance_metadata():
    runner = FakeAbxDlRunner(
        {
            "gallerydl/post.json": json.dumps(
                {
                    "caption": "Social evidence",
                    "acquisition_adapter": "forged",
                    "extractor": "forged",
                    "artifact_path": "outside/forged.txt",
                }
            )
        }
    )

    source = SourceRetriever(
        abxdl_runner=runner,
        resolver=FakeResolver(),
    ).retrieve(INSTAGRAM_URL)

    assert source.metadata["acquisition_adapter"] == "abx-dl"
    assert source.metadata["extractor"] == "gallerydl"
    assert source.metadata["artifact_path"] == "gallerydl/post.json"


def test_korean_subtitle_is_preferred_over_english_at_same_priority():
    runner = FakeAbxDlRunner(
        {
            "ytdlp/video.en.vtt": "WEBVTT\n\n00:00.000 --> 00:01.000\nEnglish",
            "ytdlp/video.ko.vtt": "WEBVTT\n\n00:00.000 --> 00:01.000\n한국어",
        }
    )

    source = SourceRetriever(
        abxdl_runner=runner,
        resolver=FakeResolver(),
    ).retrieve(YOUTUBE_URL)

    assert source.content == "한국어"


def test_nonzero_runner_result_is_sanitized_and_has_no_network_fallback():
    runner = FakeAbxDlRunner(
        {"readability/content.txt": "secret runner output"}, returncode=1
    )

    with pytest.raises(SourceError, match="^unsupported_source$") as error:
        SourceRetriever(
            abxdl_runner=runner,
            resolver=FakeResolver(),
        ).retrieve(SAFE_URL)

    assert runner.calls == 1
    assert "secret" not in str(error.value)


def test_missing_text_artifact_fails_and_temp_output_is_removed():
    runner = FakeAbxDlRunner({"screenshot/output.png": b"PNG"})

    with pytest.raises(SourceError, match="^unsupported_source$"):
        SourceRetriever(
            abxdl_runner=runner,
            resolver=FakeResolver(),
        ).retrieve(SAFE_URL)

    assert runner.cwd is not None
    assert not runner.cwd.exists()


def test_malformed_index_jsonl_fails_closed():
    class MalformedIndexRunner(FakeAbxDlRunner):
        def __call__(self, *args, **kwargs):
            result = super().__call__(*args, **kwargs)
            assert self.cwd is not None
            (self.cwd / "index.jsonl").write_text("not-json\n", encoding="utf-8")
            return result

    runner = MalformedIndexRunner({"readability/content.txt": "article"})

    with pytest.raises(SourceError, match="^unsupported_source$"):
        SourceRetriever(
            abxdl_runner=runner,
            resolver=FakeResolver(),
        ).retrieve(SAFE_URL)


def test_runner_output_root_must_match_the_temporary_directory(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "content.txt").write_text("outside evidence", encoding="utf-8")
    runner = FakeAbxDlRunner(output_root_override=outside)

    with pytest.raises(SourceError, match="^unsafe_output$"):
        SourceRetriever(
            abxdl_runner=runner,
            resolver=FakeResolver(),
        ).retrieve(SAFE_URL)


def test_output_file_count_is_bounded(monkeypatch):
    monkeypatch.setattr(module, "MAX_OUTPUT_FILES", 4)
    files = {f"readability/{index}.txt": "x" for index in range(4)}

    with pytest.raises(SourceError, match="^response_too_large$"):
        SourceRetriever(
            abxdl_runner=FakeAbxDlRunner(files),
            resolver=FakeResolver(),
        ).retrieve(SAFE_URL)


def test_output_bytes_are_bounded(monkeypatch):
    monkeypatch.setattr(module, "MAX_OUTPUT_BYTES", 32)

    with pytest.raises(SourceError, match="^response_too_large$"):
        SourceRetriever(
            abxdl_runner=FakeAbxDlRunner(
                {"readability/content.txt": "x" * 64}
            ),
            resolver=FakeResolver(),
        ).retrieve(SAFE_URL)


def test_default_runner_uses_no_shell_and_a_sanitized_environment(
    monkeypatch, tmp_path
):
    process = FakeProcess()
    captured = {}

    def fake_popen(*args, **kwargs):
        captured["args"] = args
        captured.update(kwargs)
        return process

    monkeypatch.setenv("DISCORD_BOT_TOKEN", "do-not-forward")
    monkeypatch.setenv("GITHUB_TOKEN", "do-not-forward")
    monkeypatch.setattr(module.subprocess, "Popen", fake_popen)

    result = module._run_abx_dl(
        ["abx-dl", "--no-install", "https://public.example/article"],
        cwd=str(tmp_path),
        timeout=1,
        max_bytes=1024,
        max_files=8,
    )

    assert result.returncode == 0
    assert captured["shell"] is False
    assert captured["stdout"] is subprocess.DEVNULL
    assert captured["stderr"] is subprocess.DEVNULL
    assert "DISCORD_BOT_TOKEN" not in captured["env"]
    assert "GITHUB_TOKEN" not in captured["env"]
    assert "PATH" in captured["env"]


def test_default_runner_kills_a_process_that_exceeds_output_limit(
    monkeypatch, tmp_path
):
    process = FakeProcess([None])
    (tmp_path / "large.txt").write_text("x" * 64, encoding="utf-8")
    monkeypatch.setattr(module.subprocess, "Popen", lambda *args, **kwargs: process)

    with pytest.raises(SourceError, match="^response_too_large$"):
        module._run_abx_dl(
            ["abx-dl", "--no-install", SAFE_URL],
            cwd=str(tmp_path),
            timeout=1,
            max_bytes=32,
            max_files=8,
        )

    assert process.killed is True


def test_default_runner_kills_a_timed_out_process(monkeypatch, tmp_path):
    process = FakeProcess([None, None, None])
    clock_values = iter([0.0, 2.0])
    monkeypatch.setattr(module.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(module.time, "monotonic", lambda: next(clock_values))

    with pytest.raises(SourceError, match="^timeout$"):
        module._run_abx_dl(
            ["abx-dl", "--no-install", SAFE_URL],
            cwd=str(tmp_path),
            timeout=1,
            max_bytes=1024,
            max_files=8,
        )

    assert process.killed is True


def test_output_symlinks_are_rejected_when_supported(tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")

    class SymlinkRunner(FakeAbxDlRunner):
        def __call__(self, *args, **kwargs):
            result = super().__call__(*args, **kwargs)
            assert self.cwd is not None
            link = self.cwd / "readability" / "content.txt"
            link.parent.mkdir(parents=True, exist_ok=True)
            try:
                link.symlink_to(outside)
            except OSError:
                pytest.skip("symlink creation is unavailable on this Windows host")
            return result

    with pytest.raises(SourceError, match="^unsafe_output$"):
        SourceRetriever(
            abxdl_runner=SymlinkRunner(),
            resolver=FakeResolver(),
        ).retrieve(SAFE_URL)


def test_abxdl_executable_missing_is_a_stable_dependency_error(
    monkeypatch, tmp_path
):
    def missing(*args, **kwargs):
        del args, kwargs
        raise FileNotFoundError("secret local path")

    monkeypatch.setattr(module.subprocess, "Popen", missing)

    with pytest.raises(SourceError, match="^missing_dependency$") as error:
        module._run_abx_dl(
            ["abx-dl", "--no-install", SAFE_URL],
            cwd=str(tmp_path),
            timeout=1,
            max_bytes=1024,
            max_files=8,
        )

    assert "secret" not in str(error.value)


def test_sanitized_environment_keeps_only_runtime_basics(monkeypatch):
    monkeypatch.setenv("PATH", os.environ.get("PATH", ""))
    monkeypatch.setenv("HOME", "C:/Users/example")
    monkeypatch.setenv("OPENAI_API_KEY", "secret")

    environment = module._sanitized_environment()

    assert environment["PATH"] == os.environ["PATH"]
    assert environment["HOME"] == "C:/Users/example"
    assert "OPENAI_API_KEY" not in environment
