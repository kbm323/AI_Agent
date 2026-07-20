from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

import pytest

from src.runtime_architecture_v2 import llmwiki_sources as module
from src.runtime_architecture_v2.llmwiki_sources import (  # noqa: I001
    HttpResponse,
    SourceError,
    SourceRetriever,
    extract_single_url,
    normalize_source_url,
)

SAFE_URL = "https://public.example/article?topic=llm&lang=en"
YOUTUBE_URL = "https://www.youtube.com/watch?v=video123"
YOUTUBE_JSON = json.dumps(
    {
        "id": "video123",
        "title": "A public video",
        "channel": "Example Channel",
        "uploader": "Example Channel",
        "duration": 42,
    }
)


class FakeResolver:
    def __init__(self, addresses: dict[str, tuple[str, ...]] | None = None):
        self.addresses = addresses or {"public.example": ("93.184.216.34",)}
        self.calls: list[tuple[str, int]] = []

    def __call__(self, host: str, port: int) -> tuple[str, ...]:
        self.calls.append((host, port))
        return self.addresses[host]


@dataclass
class FakeFetcher:
    responses: list[HttpResponse]

    def __post_init__(self):
        self.urls: list[str] = []

    def fetch(self, url: str, *, timeout: float, max_bytes: int) -> HttpResponse:
        self.urls.append(url)
        return self.responses.pop(0)


class AdvancingClock:
    def __init__(self, now: float = 0):
        self.now = now

    def __call__(self) -> float:
        return self.now


class BlockingResolver:
    def __call__(self, host: str, port: int) -> tuple[str, ...]:
        del host, port
        time.sleep(0.2)
        return ("93.184.216.34",)


class BlockingFetcher:
    def fetch(self, url: str, *, timeout: float, max_bytes: int) -> HttpResponse:
        del url, timeout, max_bytes
        time.sleep(0.2)
        raise AssertionError("fetch completed after deadline")


class FakeProcess:
    def __init__(self, stdout: bytes = b"", *, running: bool = False):
        self.stdout = BytesIO(stdout)
        self.returncode = None if running else 0
        self.killed = False

    def poll(self):
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self, timeout=None):
        del timeout
        return self.returncode


class FakeYtDlpRunner:
    def __init__(
        self,
        stdout: str,
        *,
        returncode: int = 0,
        vtt: str | None = None,
        vtt_files: tuple[str, ...] = (),
    ):
        self.stdout = stdout
        self.returncode = returncode
        self.vtt = vtt if vtt is not None else (
            "WEBVTT\n\n00:00.000 --> 00:01.000\ntranscript text\n"
        )
        self.vtt_files = vtt_files
        self.argv: list[str] = []

    def __call__(
        self, argv: list[str], *, cwd: str, timeout: float, max_bytes: int
    ):
        del timeout, max_bytes
        self.argv = argv
        Path(cwd, "video123.en.vtt").write_text(self.vtt, encoding="utf-8")
        for index, source in enumerate(self.vtt_files):
            Path(cwd, f"video123.{index}.vtt").write_text(source, encoding="utf-8")
        return type(
            "Result", (), {"returncode": self.returncode, "stdout": self.stdout}
        )()


@pytest.mark.parametrize("text", ["no url", "https://a.example https://b.example"])
def test_extract_single_url_rejects_missing_or_ambiguous_input(text):
    with pytest.raises(SourceError, match="invalid_url"):
        extract_single_url(text)


def test_extract_single_url_trims_ordinary_sentence_punctuation():
    assert extract_single_url("Read https://Example.com/path?x=1, please.") == (
        "https://Example.com/path?x=1"
    )


@pytest.mark.parametrize(
    "text",
    [
        "https://a.examplehttps://b.example",
        "https://a.example,https://b.example",
    ],
)
def test_extract_single_url_rejects_adjacent_or_comma_separated_urls(text):
    with pytest.raises(SourceError, match="invalid_url"):
        extract_single_url(text)


def test_normalize_source_url_sanitizes_only_nonsemantic_components():
    normalized = normalize_source_url(
        "HTTPS://Example.COM:443/a%20path?b=2&a=1&a=0#section"
    )

    assert normalized == "https://example.com/a%20path?b=2&a=1&a=0"


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/%zz",
        "https://example..com/",
        "https://example.com:0/",
        "https://example.com:/",
        "https://bad_host.example/",
        "https://user:password@example.com/",
    ],
)
def test_normalize_source_url_rejects_malformed_components_without_causes(url):
    with pytest.raises(SourceError, match="^invalid_url$") as error:
        normalize_source_url(url)

    assert error.value.__cause__ is None
    assert str(error.value) == "invalid_url"


@pytest.mark.parametrize(
    "url",
    ["file:///etc/passwd", "https://user:password@public.example/secret"],
)
def test_retriever_rejects_unsupported_or_credentialed_urls(url):
    with pytest.raises(SourceError, match="invalid_url"):
        SourceRetriever(resolver=FakeResolver()).retrieve(url)


def test_generic_retriever_rejects_private_targets_before_fetching():
    fetcher = FakeFetcher([])

    with pytest.raises(SourceError, match="unsafe_target"):
        SourceRetriever(fetcher=fetcher, resolver=FakeResolver()).retrieve(
            "http://127.0.0.1/private"
        )

    assert fetcher.urls == []


def test_generic_retriever_rejects_any_private_resolved_address():
    fetcher = FakeFetcher([])
    resolver = FakeResolver({"public.example": ("93.184.216.34", "10.0.0.8")})

    with pytest.raises(SourceError, match="unsafe_target"):
        SourceRetriever(fetcher=fetcher, resolver=resolver).retrieve(SAFE_URL)

    assert fetcher.urls == []


def test_generic_retriever_revalidates_redirect_targets_and_connected_peer():
    redirect_fetcher = FakeFetcher(
        [
            HttpResponse(
                status=302,
                headers={"location": "http://127.0.0.1/private"},
                body=b"",
                peer_address="93.184.216.34",
            )
        ]
    )
    with pytest.raises(SourceError, match="unsafe_target"):
        SourceRetriever(fetcher=redirect_fetcher, resolver=FakeResolver()).retrieve(
            SAFE_URL
        )


def test_generic_deadline_stops_before_fetch_after_resolver_uses_budget():
    clock = AdvancingClock()
    fetcher = FakeFetcher([])

    def resolver(host: str, port: int) -> tuple[str, ...]:
        del host, port
        clock.now += 31
        return ("93.184.216.34",)

    with pytest.raises(SourceError, match="^timeout$") as error:
        SourceRetriever(fetcher=fetcher, resolver=resolver, clock=clock).retrieve(
            SAFE_URL
        )

    assert error.value.__cause__ is None
    assert fetcher.urls == []


def test_generic_dns_wait_is_bounded_and_does_not_block_retrieval(monkeypatch):
    monkeypatch.setattr(module, "RETRIEVAL_TIMEOUT_SECONDS", 0.01)
    started = time.monotonic()

    with pytest.raises(SourceError, match="^timeout$"):
        SourceRetriever(fetcher=FakeFetcher([]), resolver=BlockingResolver()).retrieve(
            SAFE_URL
        )

    assert time.monotonic() - started < 0.1


def test_generic_injected_fetch_wait_is_bounded(monkeypatch):
    monkeypatch.setattr(module, "RETRIEVAL_TIMEOUT_SECONDS", 0.01)
    started = time.monotonic()

    with pytest.raises(SourceError, match="^timeout$"):
        SourceRetriever(
            fetcher=BlockingFetcher(), resolver=FakeResolver()
        ).retrieve(SAFE_URL)

    assert time.monotonic() - started < 0.1

    peer_fetcher = FakeFetcher(
        [
            HttpResponse(
                status=200,
                headers={"content-type": "text/plain"},
                body=b"safe-looking body",
                peer_address="127.0.0.1",
            )
        ]
    )
    with pytest.raises(SourceError, match="unsafe_target"):
        SourceRetriever(fetcher=peer_fetcher, resolver=FakeResolver()).retrieve(
            SAFE_URL
        )


def test_generic_retriever_strips_active_html_and_collapses_whitespace():
    fetcher = FakeFetcher(
        [
            HttpResponse(
                status=200,
                headers={"content-type": "text/html; charset=utf-8"},
                body=(
                    b"<html><head><title>Example title</title><style>hidden</style>"
                    b"</head><body> first <script>bad()</script><noscript>no</noscript>"
                    b"<template>not visible</template> second   third </body></html>"
                ),
                peer_address="93.184.216.34",
            )
        ]
    )

    source = SourceRetriever(fetcher=fetcher, resolver=FakeResolver()).retrieve(
        SAFE_URL
    )

    assert source.source_type == "web"
    assert source.title == "Example title"
    assert source.content == "first second third"
    assert source.normalized_url == SAFE_URL


def test_generic_retriever_keeps_title_out_of_body_and_rejects_empty_html():
    title_only = FakeFetcher(
        [
            HttpResponse(
                status=200,
                headers={"content-type": "text/html"},
                body=(
                    b"<title>Only a title</title><script>bad()</script>"
                    b"<style>hidden</style><noscript>fallback</noscript>"
                    b"<template>template</template>"
                ),
                peer_address="93.184.216.34",
            )
        ]
    )

    with pytest.raises(SourceError, match="^unsupported_source$") as error:
        SourceRetriever(fetcher=title_only, resolver=FakeResolver()).retrieve(SAFE_URL)

    assert error.value.__cause__ is None


@pytest.mark.parametrize(
    ("content_type", "body", "expected"),
    [
        ("application/json", b'{"title": "value"}', '{"title": "value"}'),
        ("text/plain", b"plain text\nkeeps content", "plain text\nkeeps content"),
    ],
)
def test_generic_retriever_keeps_json_and_text_bounded(content_type, body, expected):
    fetcher = FakeFetcher(
        [
            HttpResponse(
                status=200,
                headers={"content-type": content_type},
                body=body,
                peer_address="93.184.216.34",
            )
        ]
    )

    source = SourceRetriever(fetcher=fetcher, resolver=FakeResolver()).retrieve(
        SAFE_URL
    )

    assert source.content == expected


def test_generic_retriever_rejects_oversized_and_unsupported_responses():
    oversized = FakeFetcher(
        [
            HttpResponse(
                status=200,
                headers={"content-type": "text/plain"},
                body=b"x" * (10 * 1024 * 1024 + 1),
                peer_address="93.184.216.34",
            )
        ]
    )
    with pytest.raises(SourceError, match="response_too_large"):
        SourceRetriever(fetcher=oversized, resolver=FakeResolver()).retrieve(SAFE_URL)

    media = FakeFetcher(
        [
            HttpResponse(
                status=200,
                headers={"content-type": "video/mp4"},
                body=b"video",
                peer_address="93.184.216.34",
            )
        ]
    )
    with pytest.raises(SourceError, match="unsupported_source"):
        SourceRetriever(fetcher=media, resolver=FakeResolver()).retrieve(SAFE_URL)


def test_generic_retriever_limits_redirects():
    fetcher = FakeFetcher(
        [
            HttpResponse(
                status=302,
                headers={"location": f"/next-{number}"},
                body=b"",
                peer_address="93.184.216.34",
            )
            for number in range(6)
        ]
    )

    with pytest.raises(SourceError, match="too_many_redirects"):
        SourceRetriever(fetcher=fetcher, resolver=FakeResolver()).retrieve(SAFE_URL)


def test_youtube_uses_metadata_and_subtitles_without_media_download():
    runner = FakeYtDlpRunner(YOUTUBE_JSON)

    source = SourceRetriever(yt_dlp_runner=runner).retrieve(YOUTUBE_URL)

    assert "--dump-single-json" in runner.argv
    assert "--skip-download" in runner.argv
    assert "--write-auto-subs" in runner.argv
    assert "--write-subs" in runner.argv
    assert "--no-playlist" in runner.argv
    assert source.source_type == "youtube"
    assert source.title == "A public video"
    assert source.content == "transcript text"
    assert source.metadata == {
        "channel": "Example Channel",
        "duration": 42,
        "id": "video123",
        "uploader": "Example Channel",
    }


def test_youtube_vtt_headers_do_not_become_transcript_content():
    runner = FakeYtDlpRunner(
        YOUTUBE_JSON,
        vtt=(
            "WEBVTT\n"
            "Kind: captions\n"
            "Language: en\n\n"
            "00:00.000 --> 00:01.000\n"
            "transcript text\n"
        ),
    )

    source = SourceRetriever(yt_dlp_runner=runner).retrieve(YOUTUBE_URL)

    assert source.content == "transcript text"


def test_youtube_rejects_oversized_metadata_without_exposing_output():
    runner = FakeYtDlpRunner("x" * (10 * 1024 * 1024 + 1))

    with pytest.raises(SourceError, match="^response_too_large$") as error:
        SourceRetriever(yt_dlp_runner=runner).retrieve(YOUTUBE_URL)

    assert error.value.__cause__ is None
    assert "x" not in str(error.value)


def test_youtube_rejects_cumulative_oversized_vtt_files():
    cue = "WEBVTT\n\n00:00.000 --> 00:01.000\ntext\n"
    runner = FakeYtDlpRunner(
        YOUTUBE_JSON,
        vtt_files=(cue + "x" * (6 * 1024 * 1024),) * 2,
    )

    with pytest.raises(SourceError, match="^response_too_large$"):
        SourceRetriever(yt_dlp_runner=runner).retrieve(YOUTUBE_URL)


def test_youtube_shares_the_size_budget_between_metadata_and_vtt():
    metadata = json.dumps({"title": "x" * (6 * 1024 * 1024)})
    runner = FakeYtDlpRunner(
        metadata,
        vtt="WEBVTT\n\n00:00.000 --> 00:01.000\n" + "x" * (5 * 1024 * 1024),
    )

    with pytest.raises(SourceError, match="^response_too_large$"):
        SourceRetriever(yt_dlp_runner=runner).retrieve(YOUTUBE_URL)


def test_youtube_deadline_expires_before_transcript_reading(monkeypatch):
    clock = AdvancingClock()
    runner = FakeYtDlpRunner(YOUTUBE_JSON)
    original_runner = runner.__call__

    def advance_clock(*args, **kwargs):
        result = original_runner(*args, **kwargs)
        clock.now += 31
        return result

    monkeypatch.setattr(
        module,
        "_read_vtt_transcript",
        lambda *args: pytest.fail("transcript read after deadline"),
    )

    with pytest.raises(SourceError, match="^timeout$"):
        SourceRetriever(yt_dlp_runner=advance_clock, clock=clock).retrieve(YOUTUBE_URL)


def test_default_ytdlp_runner_kills_timed_out_process_and_discards_stderr(
    monkeypatch, tmp_path
):
    process = FakeProcess(running=True)
    captured = {}

    def fake_popen(*args, **kwargs):
        captured.update(kwargs)
        return process

    monkeypatch.setattr(module.subprocess, "Popen", fake_popen)

    with pytest.raises(TimeoutError):
        module._run_yt_dlp(
            ["yt-dlp", "--version"],
            cwd=str(tmp_path),
            timeout=0.01,
            max_bytes=1024,
        )

    assert process.killed is True
    assert captured["stderr"] is subprocess.DEVNULL
    assert captured["stdout"] is subprocess.PIPE


def test_youtube_posts_use_generic_fetcher_instead_of_yt_dlp():
    runner = FakeYtDlpRunner(YOUTUBE_JSON)
    fetcher = FakeFetcher(
        [
            HttpResponse(
                status=200,
                headers={"content-type": "text/plain"},
                body=b"A public post",
                peer_address="142.250.72.46",
            )
        ]
    )
    resolver = FakeResolver({"www.youtube.com": ("142.250.72.46",)})

    source = SourceRetriever(
        fetcher=fetcher, resolver=resolver, yt_dlp_runner=runner
    ).retrieve("https://www.youtube.com/post/abc123")

    assert source.source_type == "web"
    assert source.content == "A public post"
    assert runner.argv == []


@pytest.mark.parametrize(
    "runner",
    [
        FakeYtDlpRunner("not json"),
        FakeYtDlpRunner(YOUTUBE_JSON, returncode=1),
    ],
)
def test_youtube_failures_are_stable_and_do_not_expose_runner_output(runner):
    with pytest.raises(SourceError, match="unsupported_source") as error:
        SourceRetriever(yt_dlp_runner=runner).retrieve(YOUTUBE_URL)

    assert "not json" not in str(error.value)


def test_host_header_brackets_ipv6_literals_and_preserves_non_default_port():
    assert module._host_header("2001:4860:4860::8888", 443, "https") == (
        "[2001:4860:4860::8888]"
    )
    assert module._host_header("2001:4860:4860::8888", 8443, "https") == (
        "[2001:4860:4860::8888]:8443"
    )
