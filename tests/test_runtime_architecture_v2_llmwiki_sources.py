from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

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


class FakeYtDlpRunner:
    def __init__(self, stdout: str, *, returncode: int = 0, vtt: str | None = None):
        self.stdout = stdout
        self.returncode = returncode
        self.vtt = vtt if vtt is not None else (
            "WEBVTT\n\n00:00.000 --> 00:01.000\ntranscript text\n"
        )
        self.argv: list[str] = []

    def __call__(self, argv: list[str], *, cwd: str, timeout: float):
        self.argv = argv
        Path(cwd, "video123.en.vtt").write_text(self.vtt, encoding="utf-8")
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


def test_normalize_source_url_sanitizes_only_nonsemantic_components():
    normalized = normalize_source_url(
        "HTTPS://Example.COM:443/a%20path?b=2&a=1&a=0#section"
    )

    assert normalized == "https://example.com/a%20path?b=2&a=1&a=0"


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
    assert source.content == "Example title first second third"
    assert source.normalized_url == SAFE_URL


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
