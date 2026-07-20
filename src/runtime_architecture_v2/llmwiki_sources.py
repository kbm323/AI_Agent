"""Bounded, SSRF-safe retrieval of public LLM Wiki sources."""

from __future__ import annotations

import html
import http.client
import ipaddress
import json
import re
import socket
import subprocess
import tempfile
import threading
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


@dataclass(frozen=True)
class _YtDlpResult:
    returncode: int
    stdout: str


def extract_single_url(text: str) -> str:
    """Return the one URL in user text, rejecting ambiguous input."""

    if not isinstance(text, str):
        raise SourceError("invalid_url")
    starts = list(_URL_START.finditer(text))
    matches = []
    for index, start in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else len(text)
        candidate = _URL_TOKEN.match(text[start.start() : end])
        if candidate is None:
            continue
        trimmed = candidate.group().rstrip(".,;:!?")
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
    if _MALFORMED_PERCENT.search(url):
        raise SourceError("invalid_url")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        raise SourceError("invalid_url") from None
    scheme = parsed.scheme.lower()
    host = parsed.hostname
    if (
        scheme not in {"http", "https"}
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.netloc.endswith(":")
        or port == 0
    ):
        raise SourceError("invalid_url")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is None:
        host = _normalize_domain_name(host)
    else:
        host = address.compressed.lower()
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
        self._fetcher = fetcher or _StdlibHttpFetcher()
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
            addresses = _validate_public_target(
                current_url, self._resolver, deadline, self._clock
            )
            response = self._fetch(
                current_url,
                _remaining(deadline, self._clock),
                addresses,
            )
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

    def _fetch(
        self, url: str, remaining: float, addresses: Sequence[str]
    ) -> HttpResponse:
        try:
            if isinstance(self._fetcher, _StdlibHttpFetcher):
                response = self._fetcher.fetch(
                    url,
                    timeout=remaining,
                    max_bytes=MAX_RESPONSE_BYTES,
                    addresses=addresses,
                )
            else:
                fetch = getattr(self._fetcher, "fetch", self._fetcher)
                response = _fetch_bounded(
                    fetch,
                    url,
                    remaining,
                    MAX_RESPONSE_BYTES,
                )
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
                    "--no-playlist",
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
                    arguments,
                    cwd=directory,
                    timeout=_remaining(deadline, self._clock),
                    max_bytes=MAX_RESPONSE_BYTES,
                )
                if getattr(result, "returncode", 1) != 0:
                    raise SourceError("unsupported_source")
                stdout = getattr(result, "stdout", "")
                metadata_bytes = _ensure_text_limit(stdout, MAX_RESPONSE_BYTES)
                metadata = _parse_youtube_metadata(stdout)
                _remaining(deadline, self._clock)
                transcript = _read_vtt_transcript(
                    Path(directory),
                    MAX_RESPONSE_BYTES - metadata_bytes,
                    deadline,
                    self._clock,
                )
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

    def fetch(
        self,
        url: str,
        *,
        timeout: float,
        max_bytes: int,
        addresses: Sequence[str],
    ) -> HttpResponse:
        deadline = time.monotonic() + timeout
        parsed = urlsplit(url)
        host = parsed.hostname
        if host is None:
            raise SourceError("invalid_url")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
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
            if content_length is not None:
                try:
                    if int(content_length) > max_bytes:
                        raise SourceError("response_too_large")
                except ValueError:
                    raise SourceError("retrieval_failed") from None
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
        if self._in_title:
            self.title.append(data)
            return
        self.text.append(data)


def _parse_youtube_metadata(stdout: object) -> dict[str, object]:
    if not isinstance(stdout, str):
        raise SourceError("unsupported_source")
    try:
        data = json.loads(stdout)
    except (TypeError, ValueError):
        raise SourceError("unsupported_source") from None
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


def _read_vtt_transcript(
    directory: Path,
    max_bytes: int,
    deadline: float,
    clock: Callable[[], float],
) -> str:
    lines: list[str] = []
    total_bytes = 0
    for path in sorted(directory.rglob("*.vtt")):
        _remaining(deadline, clock)
        try:
            source, total_bytes = _read_limited_text(
                path, total_bytes, max_bytes, deadline, clock
            )
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


def _validate_public_target(
    url: str,
    resolver: Resolver,
    deadline: float,
    clock: Callable[[], float],
) -> tuple[str, ...]:
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
        return (str(literal_address),)
    addresses = _resolve_bounded(
        resolver,
        host,
        parsed.port or (443 if parsed.scheme == "https" else 80),
        deadline,
        clock,
    )
    _validate_addresses(addresses)
    return addresses


def _resolve_public_host(host: str, port: int) -> tuple[str, ...]:
    records = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
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
    except ValueError:
        raise SourceError("unsafe_target") from None
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
    argv: list[str], *, cwd: str, timeout: float, max_bytes: int
) -> _YtDlpResult:
    process = subprocess.Popen(
        argv,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        shell=False,
    )
    if process.stdout is None:
        _kill_process(process)
        raise OSError("missing_stdout")
    deadline = time.monotonic() + timeout
    chunks: list[bytes] = []
    size = 0
    overflow = threading.Event()
    complete = threading.Event()

    def drain_stdout() -> None:
        nonlocal size
        try:
            while chunk := process.stdout.read(64 * 1024):
                if size + len(chunk) > max_bytes:
                    overflow.set()
                    _kill_process(process)
                    return
                chunks.append(chunk)
                size += len(chunk)
        finally:
            complete.set()

    threading.Thread(target=drain_stdout, daemon=True).start()
    while process.poll() is None:
        if overflow.is_set():
            _kill_process(process)
            raise SourceError("response_too_large")
        if time.monotonic() >= deadline:
            _kill_process(process)
            raise TimeoutError
        time.sleep(0.005)
    if not complete.wait(_deadline_remaining(deadline)):
        _kill_process(process)
        raise TimeoutError
    if overflow.is_set():
        raise SourceError("response_too_large")
    return _YtDlpResult(process.returncode or 0, b"".join(chunks).decode("utf-8"))


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


def _resolve_bounded(
    resolver: Resolver,
    host: str,
    port: int,
    deadline: float,
    clock: Callable[[], float],
) -> tuple[str, ...]:
    result: list[Sequence[str]] = []
    failed = threading.Event()
    completed = threading.Event()

    def resolve() -> None:
        try:
            result.append(resolver(host, port))
        except Exception:
            failed.set()
        finally:
            completed.set()

    threading.Thread(target=resolve, daemon=True).start()
    if not completed.wait(_remaining(deadline, clock)):
        raise SourceError("timeout")
    _remaining(deadline, clock)
    if failed.is_set() or not result:
        raise SourceError("retrieval_failed")
    return tuple(result[0])


def _fetch_bounded(
    fetch: Callable[..., HttpResponse],
    url: str,
    timeout: float,
    max_bytes: int,
) -> HttpResponse:
    result: list[HttpResponse] = []
    failure: list[BaseException] = []
    completed = threading.Event()

    def run_fetch() -> None:
        try:
            result.append(fetch(url, timeout=timeout, max_bytes=max_bytes))
        except BaseException as exc:
            failure.append(exc)
        finally:
            completed.set()

    threading.Thread(target=run_fetch, daemon=True).start()
    if not completed.wait(timeout):
        raise SourceError("timeout")
    if failure:
        if isinstance(failure[0], SourceError):
            raise SourceError(failure[0].code) from None
        if isinstance(failure[0], TimeoutError):
            raise SourceError("timeout") from None
        raise SourceError("retrieval_failed") from None
    if not result:
        raise SourceError("retrieval_failed")
    return result[0]


def _normalize_domain_name(host: str) -> str:
    if host.endswith("."):
        raise SourceError("invalid_url")
    labels = host.split(".")
    if any(not label for label in labels):
        raise SourceError("invalid_url")
    normalized = []
    for label in labels:
        try:
            ascii_label = label.encode("idna").decode("ascii").lower()
        except UnicodeError:
            raise SourceError("invalid_url") from None
        if (
            not 1 <= len(ascii_label) <= 63
            or not ascii_label[0].isalnum()
            or not ascii_label[-1].isalnum()
            or any(
                not (character.isalnum() or character == "-")
                for character in ascii_label
            )
        ):
            raise SourceError("invalid_url")
        normalized.append(ascii_label)
    return ".".join(normalized)


def _ensure_text_limit(value: object, max_bytes: int) -> int:
    if not isinstance(value, str):
        raise SourceError("unsupported_source")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeError:
        raise SourceError("unsupported_source") from None
    if size > max_bytes:
        raise SourceError("response_too_large")
    return size


def _read_limited_text(
    path: Path,
    total: int,
    max_bytes: int,
    deadline: float,
    clock: Callable[[], float],
) -> tuple[str, int]:
    chunks = []
    with path.open("rb") as handle:
        while chunk := handle.read(64 * 1024):
            _remaining(deadline, clock)
            total += len(chunk)
            if total > max_bytes:
                raise SourceError("response_too_large")
            chunks.append(chunk)
    return b"".join(chunks).decode("utf-8", errors="replace"), total


def _kill_process(process: subprocess.Popen[bytes]) -> None:
    try:
        if process.poll() is None:
            process.kill()
    except Exception:
        return


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
    display_host = f"[{host}]" if ":" in host else host
    return display_host if port == default_port else f"{display_host}:{port}"


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


_MALFORMED_PERCENT = re.compile(r"%(?![0-9A-Fa-f]{2})")
_URL_START = re.compile(r"https?://", re.IGNORECASE)
_URL_TOKEN = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
