"""Bounded, SSRF-safe retrieval of public LLM Wiki sources."""

from __future__ import annotations

import html
import http.client
import ipaddress
import json
import socket
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

from .llmwiki_models import LlmWikiSource

MAX_REDIRECTS = 5
MAX_RESPONSE_BYTES = 10 * 1024 * 1024
RETRIEVAL_TIMEOUT_SECONDS = 30.0


class SourceError(Exception):
    """A public failure whose text is a stable, non-sensitive code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class HttpResponse:
    """The bounded output of one HTTP request without redirect following."""

    status: int
    headers: Mapping[str, str]
    body: bytes
    peer_address: str | None


Resolver = Callable[[str, int], Sequence[str]]
YtDlpRunner = Callable[..., Any]


def extract_single_url(text: str) -> str:
    """Return the one URL in user text, rejecting ambiguous input."""

    if not isinstance(text, str):
        raise SourceError("invalid_url")
    matches = []
    for candidate in _URL_CANDIDATES(text):
        trimmed = candidate.rstrip(".,;:!?")
        if trimmed:
            matches.append(trimmed)
    if len(matches) != 1:
        raise SourceError("invalid_url")
    normalize_source_url(matches[0])
    return matches[0]


def normalize_source_url(url: str) -> str:
    """Normalize stable URL components while preserving semantic query order."""

    if (
        not isinstance(url, str)
        or not url
        or any(character.isspace() for character in url)
    ):
        raise SourceError("invalid_url")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise SourceError("invalid_url") from exc
    scheme = parsed.scheme.lower()
    host = parsed.hostname
    if (
        scheme not in {"http", "https"}
        or not host
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise SourceError("invalid_url")
    try:
        host = host.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise SourceError("invalid_url") from exc
    default_port = 80 if scheme == "http" else 443
    display_host = f"[{host}]" if ":" in host else host
    netloc = display_host if port in {None, default_port} else f"{display_host}:{port}"
    return urlunsplit((scheme, netloc, parsed.path, parsed.query, ""))


class SourceRetriever:
    """Retrieve only public URLs through injected or standard-library transports."""

    def __init__(
        self,
        *,
        fetcher: Any | None = None,
        yt_dlp_runner: YtDlpRunner | None = None,
        resolver: Resolver | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._resolver = resolver or _resolve_public_host
        self._fetcher = fetcher or _StdlibHttpFetcher(self._resolver)
        self._yt_dlp_runner = yt_dlp_runner or _run_yt_dlp
        self._clock = clock

    def retrieve(self, url: str) -> LlmWikiSource:
        normalized_url = normalize_source_url(url)
        if _is_youtube_video(normalized_url):
            return self._retrieve_youtube(normalized_url)
        return self._retrieve_generic(normalized_url)

    def _retrieve_generic(self, normalized_url: str) -> LlmWikiSource:
        deadline = self._clock() + RETRIEVAL_TIMEOUT_SECONDS
        current_url = normalized_url
        redirects = 0
        while True:
            _validate_public_target(current_url, self._resolver)
            response = self._fetch(current_url, _remaining(deadline, self._clock))
            _validate_peer_address(response.peer_address)
            if len(response.body) > MAX_RESPONSE_BYTES:
                raise SourceError("response_too_large")
            if self._clock() > deadline:
                raise SourceError("timeout")
            if 300 <= response.status < 400:
                location = _header(response.headers, "location")
                if not location:
                    raise SourceError("retrieval_failed")
                if redirects >= MAX_REDIRECTS:
                    raise SourceError("too_many_redirects")
                current_url = normalize_source_url(urljoin(current_url, location))
                redirects += 1
                continue
            if response.status in {401, 403}:
                raise SourceError("unsupported_source")
            if not 200 <= response.status < 300:
                raise SourceError("retrieval_failed")
            return _to_web_source(current_url, response)

    def _fetch(self, url: str, remaining: float) -> HttpResponse:
        try:
            fetch = getattr(self._fetcher, "fetch", self._fetcher)
            response = fetch(url, timeout=remaining, max_bytes=MAX_RESPONSE_BYTES)
        except SourceError:
            raise
        except TimeoutError:
            raise SourceError("timeout") from None
        except Exception:
            raise SourceError("retrieval_failed") from None
        if not isinstance(response, HttpResponse):
            raise SourceError("retrieval_failed")
        return response

    def _retrieve_youtube(self, normalized_url: str) -> LlmWikiSource:
        deadline = self._clock() + RETRIEVAL_TIMEOUT_SECONDS
        try:
            with tempfile.TemporaryDirectory(prefix="llmwiki-ytdlp-") as directory:
                arguments = [
                    "yt-dlp",
                    "--dump-single-json",
                    "--skip-download",
                    "--write-auto-subs",
                    "--write-subs",
                    "--sub-langs",
                    "ko,en",
                    "--sub-format",
                    "vtt",
                    "--paths",
                    directory,
                    normalized_url,
                ]
                result = self._yt_dlp_runner(
                    arguments, cwd=directory, timeout=_remaining(deadline, self._clock)
                )
                if getattr(result, "returncode", 1) != 0:
                    raise SourceError("unsupported_source")
                metadata = _parse_youtube_metadata(getattr(result, "stdout", ""))
                transcript = _read_vtt_transcript(Path(directory))
        except SourceError:
            raise
        except (TimeoutError, subprocess.TimeoutExpired):
            raise SourceError("timeout") from None
        except Exception:
            raise SourceError("unsupported_source") from None
        if self._clock() > deadline:
            raise SourceError("timeout")
        if not transcript:
            raise SourceError("unsupported_source")
        return LlmWikiSource(
            normalized_url=normalized_url,
            source_type="youtube",
            title=str(metadata.pop("title", "YouTube video")),
            content=transcript,
            retrieved_at=_retrieved_at(),
            metadata=metadata,
        )


class _StdlibHttpFetcher:
    """HTTP transport that connects only to prevalidated DNS addresses."""

    def __init__(self, resolver: Resolver) -> None:
        self._resolver = resolver

    def fetch(self, url: str, *, timeout: float, max_bytes: int) -> HttpResponse:
        deadline = time.monotonic() + timeout
        parsed = urlsplit(url)
        host = parsed.hostname
        if host is None:
            raise SourceError("invalid_url")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        addresses = tuple(self._resolver(host, port))
        _validate_addresses(addresses)
        connection: http.client.HTTPConnection
        if parsed.scheme == "https":
            connection = _ValidatedHTTPSConnection(host, port, addresses, deadline)
        else:
            connection = _ValidatedHTTPConnection(host, port, addresses, deadline)
        target = parsed.path or "/"
        if parsed.query:
            target = f"{target}?{parsed.query}"
        try:
            connection.request(
                "GET",
                target,
                headers={
                    "Host": _host_header(host, port, parsed.scheme),
                    "User-Agent": "Oracle-LLMWiki/1.0",
                },
            )
            response = connection.getresponse()
            content_length = response.getheader("content-length")
            if content_length is not None and int(content_length) > max_bytes:
                raise SourceError("response_too_large")
            body = _read_bounded(response, max_bytes, deadline, connection.sock)
            peer = connection.sock.getpeername()[0] if connection.sock else None
            return HttpResponse(
                status=response.status,
                headers={key.lower(): value for key, value in response.getheaders()},
                body=body,
                peer_address=peer,
            )
        finally:
            connection.close()


class _ValidatedHTTPConnection(http.client.HTTPConnection):
    def __init__(
        self, host: str, port: int, addresses: Sequence[str], deadline: float
    ) -> None:
        super().__init__(host, port, timeout=_deadline_remaining(deadline))
        self._addresses = addresses
        self._deadline = deadline

    def connect(self) -> None:
        self.sock = _connect_to_public_address(
            self._addresses, self.port, self._deadline
        )


class _ValidatedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(
        self, host: str, port: int, addresses: Sequence[str], deadline: float
    ) -> None:
        super().__init__(host, port, timeout=_deadline_remaining(deadline))
        self._addresses = addresses
        self._deadline = deadline

    def connect(self) -> None:
        raw_socket = _connect_to_public_address(
            self._addresses, self.port, self._deadline
        )
        try:
            self.sock = self._context.wrap_socket(raw_socket, server_hostname=self.host)
            _validate_peer_address(self.sock.getpeername()[0])
        except Exception:
            raw_socket.close()
            raise


def _connect_to_public_address(
    addresses: Sequence[str], port: int, deadline: float
) -> socket.socket:
    last_error: OSError | None = None
    for address in addresses:
        _validate_address(address)
        try:
            sock = socket.create_connection(
                (address, port), timeout=_deadline_remaining(deadline)
            )
        except OSError as exc:
            last_error = exc
            continue
        try:
            _validate_peer_address(sock.getpeername()[0])
            return sock
        except Exception:
            sock.close()
            raise
    if last_error is not None:
        raise last_error
    raise OSError("no_public_address")


def _read_bounded(
    response: http.client.HTTPResponse,
    max_bytes: int,
    deadline: float,
    sock: socket.socket | None,
) -> bytes:
    chunks = []
    size = 0
    while True:
        if sock is not None:
            sock.settimeout(_deadline_remaining(deadline))
        chunk = response.read(min(64 * 1024, max_bytes - size + 1))
        if not chunk:
            return b"".join(chunks)
        size += len(chunk)
        if size > max_bytes:
            raise SourceError("response_too_large")
        chunks.append(chunk)


def _to_web_source(url: str, response: HttpResponse) -> LlmWikiSource:
    content_type = _header(response.headers, "content-type").split(";", 1)[0].lower()
    text = _decode_body(response.body, _header(response.headers, "content-type"))
    if content_type in {"text/html", "application/xhtml+xml"}:
        parser = _VisibleHtmlParser()
        try:
            parser.feed(text)
            parser.close()
        except Exception:
            raise SourceError("unsupported_source") from None
        content = _collapse_whitespace(" ".join(parser.text))
        title = _collapse_whitespace(" ".join(parser.title)) or _title_from_url(url)
    elif content_type.startswith("text/") or content_type in {
        "application/json",
        "application/ld+json",
    } or content_type.endswith("+json"):
        content = text
        title = _title_from_url(url)
    else:
        raise SourceError("unsupported_source")
    if not content.strip():
        raise SourceError("unsupported_source")
    return LlmWikiSource(
        normalized_url=url,
        source_type="web",
        title=title,
        content=content,
        retrieved_at=_retrieved_at(),
        metadata={"content_type": content_type, "status_code": response.status},
    )


class _VisibleHtmlParser(HTMLParser):
    _IGNORED = {"script", "style", "noscript", "template"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text: list[str] = []
        self.title: list[str] = []
        self._ignored_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag in self._IGNORED:
            self._ignored_depth += 1
        elif tag == "title" and not self._ignored_depth:
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag in self._IGNORED and self._ignored_depth:
            self._ignored_depth -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        self.text.append(data)
        if self._in_title:
            self.title.append(data)


def _parse_youtube_metadata(stdout: object) -> dict[str, object]:
    if not isinstance(stdout, str):
        raise SourceError("unsupported_source")
    try:
        data = json.loads(stdout)
    except (TypeError, ValueError) as exc:
        raise SourceError("unsupported_source") from exc
    if not isinstance(data, dict):
        raise SourceError("unsupported_source")
    metadata = {
        key: data[key]
        for key in ("id", "channel", "uploader", "duration")
        if key in data and isinstance(data[key], str | int | float)
    }
    if isinstance(data.get("title"), str) and data["title"].strip():
        metadata["title"] = data["title"].strip()
    return metadata


def _read_vtt_transcript(directory: Path) -> str:
    lines: list[str] = []
    for path in sorted(directory.rglob("*.vtt")):
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        in_cues = False
        for line in source.splitlines():
            candidate = line.strip()
            if "-->" in candidate:
                in_cues = True
                continue
            if not in_cues or not candidate:
                continue
            if candidate.isdecimal() or candidate.startswith(
                ("NOTE", "STYLE", "REGION")
            ):
                continue
            visible = _strip_html_tags(candidate)
            if visible:
                lines.append(visible)
    return _collapse_whitespace(" ".join(lines))


def _is_youtube_video(url: str) -> bool:
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    if host == "youtu.be":
        return bool(parsed.path.strip("/"))
    if host not in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
        return False
    if parsed.path == "/watch":
        return any(
            part.startswith("v=") and len(part) > 2
            for part in parsed.query.split("&")
        )
    return parsed.path.startswith("/shorts/") and bool(
        parsed.path.removeprefix("/shorts/")
    )


def _validate_public_target(url: str, resolver: Resolver) -> None:
    parsed = urlsplit(url)
    host = parsed.hostname
    if host is None:
        raise SourceError("invalid_url")
    try:
        literal_address = ipaddress.ip_address(host)
    except ValueError:
        literal_address = None
    if literal_address is not None:
        _validate_address(str(literal_address))
        return
    try:
        addresses = resolver(
            host, parsed.port or (443 if parsed.scheme == "https" else 80)
        )
    except SourceError:
        raise
    except Exception:
        raise SourceError("retrieval_failed") from None
    _validate_addresses(addresses)


def _resolve_public_host(host: str, port: int) -> tuple[str, ...]:
    try:
        records = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise SourceError("retrieval_failed") from exc
    return tuple(dict.fromkeys(record[4][0] for record in records))


def _validate_addresses(addresses: Sequence[str]) -> None:
    if not addresses:
        raise SourceError("retrieval_failed")
    for address in addresses:
        _validate_address(address)


def _validate_peer_address(address: str | None) -> None:
    if not address:
        raise SourceError("retrieval_failed")
    _validate_address(address)


def _validate_address(address: str) -> None:
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError as exc:
        raise SourceError("unsafe_target") from exc
    if (
        not parsed.is_global
        or parsed.is_private
        or parsed.is_loopback
        or parsed.is_link_local
        or parsed.is_multicast
        or parsed.is_reserved
        or parsed.is_unspecified
    ):
        raise SourceError("unsafe_target")


def _run_yt_dlp(
    argv: list[str], *, cwd: str, timeout: float
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        shell=False,
        check=False,
    )


def _remaining(deadline: float, clock: Callable[[], float]) -> float:
    remaining = deadline - clock()
    if remaining <= 0:
        raise SourceError("timeout")
    return remaining


def _deadline_remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError
    return remaining


def _header(headers: Mapping[str, str], name: str) -> str:
    for key, value in headers.items():
        if key.lower() == name:
            return value
    return ""


def _decode_body(body: bytes, content_type: str) -> str:
    charset = "utf-8"
    for parameter in content_type.split(";")[1:]:
        key, separator, value = parameter.partition("=")
        if separator and key.strip().lower() == "charset" and value.strip():
            charset = value.strip().strip('"')
            break
    try:
        return body.decode(charset, errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


def _host_header(host: str, port: int, scheme: str) -> str:
    default_port = 443 if scheme == "https" else 80
    return host if port == default_port else f"{host}:{port}"


def _title_from_url(url: str) -> str:
    return urlsplit(url).hostname or "Public source"


def _retrieved_at() -> str:
    return datetime.now(UTC).isoformat()


def _collapse_whitespace(text: str) -> str:
    return " ".join(html.unescape(text).split())


def _strip_html_tags(text: str) -> str:
    parser = _VisibleHtmlParser()
    parser.feed(text)
    parser.close()
    return _collapse_whitespace(" ".join(parser.text))


def _url_candidates(text: str) -> list[str]:
    import re

    return re.findall(r"https?://[^\s<>\"']+", text, flags=re.IGNORECASE)


_URL_CANDIDATES = _url_candidates
