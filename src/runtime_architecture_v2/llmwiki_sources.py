"""Bounded retrieval of public LLM Wiki sources through abx-dl."""

from __future__ import annotations

import html
import ipaddress
import json
import os
import re
import signal
import socket
import stat
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
from urllib.parse import urlsplit, urlunsplit

from .llmwiki_models import LlmWikiSource

MAX_OUTPUT_BYTES = 10 * 1024 * 1024
MAX_OUTPUT_FILES = 256
RETRIEVAL_TIMEOUT_SECONDS = 120.0


class SourceError(Exception):
    """A public failure whose text is a stable, non-sensitive code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class AbxDlRunResult:
    """The non-sensitive result of one abx-dl process."""

    returncode: int
    output_root: Path


@dataclass(frozen=True)
class _Artifact:
    priority: int
    language_priority: int
    relative_path: str
    extractor: str
    kind: str
    content: str
    title: str | None = None
    metadata: Mapping[str, object] | None = None


Resolver = Callable[[str, int], Sequence[str]]
AbxDlRunner = Callable[..., AbxDlRunResult]


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
    """Retrieve public URLs through one isolated abx-dl adapter invocation."""

    def __init__(
        self,
        *,
        abxdl_runner: AbxDlRunner | None = None,
        resolver: Resolver | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._abxdl_runner = abxdl_runner or _run_abx_dl
        self._resolver = resolver or _resolve_public_host
        self._clock = clock

    def retrieve(self, url: str) -> LlmWikiSource:
        normalized_url = normalize_source_url(url)
        deadline = self._clock() + RETRIEVAL_TIMEOUT_SECONDS
        _validate_public_target(
            normalized_url,
            self._resolver,
            deadline,
            self._clock,
        )

        with tempfile.TemporaryDirectory(prefix="llmwiki-abxdl-") as directory:
            root = Path(directory).resolve()
            argv = [
                "abx-dl",
                "--no-install",
                f"--dir={root}",
                normalized_url,
            ]
            try:
                result = self._abxdl_runner(
                    argv,
                    cwd=str(root),
                    timeout=_remaining(deadline, self._clock),
                    max_bytes=MAX_OUTPUT_BYTES,
                    max_files=MAX_OUTPUT_FILES,
                )
            except SourceError:
                raise
            except FileNotFoundError:
                raise SourceError("missing_dependency") from None
            except TimeoutError:
                raise SourceError("timeout") from None
            except Exception:
                raise SourceError("retrieval_failed") from None

            if not isinstance(result, AbxDlRunResult):
                raise SourceError("retrieval_failed")
            if result.output_root.resolve() != root:
                raise SourceError("unsafe_output")
            if result.returncode != 0:
                raise SourceError("unsupported_source")

            files = _collect_output_files(root, MAX_OUTPUT_BYTES, MAX_OUTPUT_FILES)
            _validate_index_jsonl(root, files)
            title = _read_capture_title(root, files)
            artifact = _select_artifact(root, files)
            if artifact is None:
                raise SourceError("unsupported_source")

            metadata = dict(artifact.metadata or {})
            metadata.update({
                "acquisition_adapter": "abx-dl",
                "extractor": artifact.extractor,
                "artifact_path": artifact.relative_path,
                "artifact_kind": artifact.kind,
            })
            return LlmWikiSource(
                normalized_url=normalized_url,
                source_type=_source_type(artifact.extractor),
                title=title or artifact.title or _title_from_url(normalized_url),
                content=artifact.content,
                retrieved_at=_retrieved_at(),
                metadata=metadata,
            )


def _run_abx_dl(
    argv: list[str],
    *,
    cwd: str,
    timeout: float,
    max_bytes: int,
    max_files: int,
) -> AbxDlRunResult:
    """Run abx-dl without a shell while bounding time and disk output."""

    popen_options: dict[str, Any] = {
        "cwd": cwd,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "shell": False,
        "env": _sanitized_environment(),
    }
    if os.name == "nt":
        popen_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_options["start_new_session"] = True
    try:
        process = subprocess.Popen(argv, **popen_options)
    except FileNotFoundError:
        raise SourceError("missing_dependency") from None
    except OSError:
        raise SourceError("retrieval_failed") from None

    deadline = time.monotonic() + timeout
    root = Path(cwd).resolve()
    try:
        while process.poll() is None:
            _measure_output_tree(root, max_bytes, max_files)
            if time.monotonic() >= deadline:
                raise SourceError("timeout")
            time.sleep(0.01)
        _measure_output_tree(root, max_bytes, max_files)
    except SourceError:
        _terminate_process_tree(process)
        raise

    return AbxDlRunResult(returncode=process.returncode or 0, output_root=root)


def _sanitized_environment() -> dict[str, str]:
    """Forward only runtime basics, excluding credentials and agent secrets."""

    allowed = {
        "APPDATA",
        "HOME",
        "LANG",
        "LC_ALL",
        "LOCALAPPDATA",
        "PATH",
        "SSL_CERT_DIR",
        "SSL_CERT_FILE",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "TMPDIR",
        "USERPROFILE",
        "WINDIR",
    }
    environment = {key: value for key, value in os.environ.items() if key in allowed}
    environment.setdefault("PATH", os.defpath)
    environment["NO_COLOR"] = "1"
    environment["PYTHONUNBUFFERED"] = "1"
    return environment


def _terminate_process_tree(process: Any) -> None:
    try:
        if process.poll() is not None:
            return
        if os.name != "nt" and getattr(process, "pid", None):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                process.kill()
        else:
            process.kill()
        process.wait(timeout=1)
    except Exception:
        return


def _measure_output_tree(root: Path, max_bytes: int, max_files: int) -> None:
    count = 0
    total = 0
    try:
        for current, directories, filenames in os.walk(root, followlinks=False):
            current_path = Path(current)
            for name in directories:
                if (current_path / name).is_symlink():
                    raise SourceError("unsafe_output")
            for name in filenames:
                path = current_path / name
                if path.is_symlink():
                    raise SourceError("unsafe_output")
                status = path.stat(follow_symlinks=False)
                if not stat.S_ISREG(status.st_mode):
                    raise SourceError("unsafe_output")
                count += 1
                total += status.st_size
                if count > max_files or total > max_bytes:
                    raise SourceError("response_too_large")
    except SourceError:
        raise
    except OSError:
        raise SourceError("unsafe_output") from None


def _collect_output_files(
    root: Path, max_bytes: int, max_files: int
) -> tuple[Path, ...]:
    _measure_output_tree(root, max_bytes, max_files)
    files: list[Path] = []
    try:
        for path in root.rglob("*"):
            if path.is_symlink():
                raise SourceError("unsafe_output")
            if not path.is_file():
                continue
            resolved = path.resolve()
            if not resolved.is_relative_to(root):
                raise SourceError("unsafe_output")
            files.append(path)
    except SourceError:
        raise
    except OSError:
        raise SourceError("unsafe_output") from None
    return tuple(sorted(files, key=lambda item: item.as_posix().lower()))


def _validate_index_jsonl(root: Path, files: Sequence[Path]) -> None:
    index = root / "index.jsonl"
    if index not in files:
        raise SourceError("unsupported_source")
    try:
        lines = index.read_text(encoding="utf-8").splitlines()
        if not lines:
            raise ValueError
        for line in lines:
            if not line.strip() or not isinstance(json.loads(line), dict):
                raise ValueError
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        raise SourceError("unsupported_source") from None


def _read_capture_title(root: Path, files: Sequence[Path]) -> str | None:
    title_path = root / "title" / "title.txt"
    if title_path not in files:
        return None
    try:
        title = title_path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    return _collapse_whitespace(title) or None


def _select_artifact(root: Path, files: Sequence[Path]) -> _Artifact | None:
    candidates = []
    for path in files:
        relative = path.relative_to(root).as_posix()
        if relative in {"index.jsonl", "title/title.txt"}:
            continue
        candidate = _artifact_from_file(path, relative)
        if candidate is not None and candidate.content.strip():
            candidates.append(candidate)
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda item: (
            item.priority,
            item.language_priority,
            item.relative_path.lower(),
        ),
    )


def _artifact_from_file(path: Path, relative: str) -> _Artifact | None:
    suffix = path.suffix.lower()
    extractor = relative.split("/", 1)[0].lower() if "/" in relative else "unknown"
    if suffix not in {".htm", ".html", ".json", ".md", ".srt", ".txt", ".vtt"}:
        return None
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    language_priority = _language_priority(path.name)
    if suffix in {".vtt", ".srt"}:
        content = _parse_subtitle(raw)
        return _Artifact(
            0,
            language_priority,
            relative,
            extractor,
            "transcript",
            content,
        )

    title = None
    metadata: Mapping[str, object] | None = None
    if suffix == ".json":
        content, title, metadata = _read_json_evidence(raw)
    elif suffix in {".html", ".htm"}:
        content, title = _read_html_evidence(raw)
    else:
        content = raw.strip()

    clean_extractors = {"defuddle", "mercury", "readability", "trafilatura"}
    social_extractors = {"gallerydl", "ytdlp"}
    dom_extractors = {"dom", "htmltotext", "singlefile"}
    if extractor in clean_extractors:
        priority, kind = 10, "clean_text"
    elif extractor in social_extractors:
        priority, kind = 20, "metadata_text"
    elif extractor in dom_extractors:
        priority, kind = 30, "rendered_text"
    else:
        priority, kind = 40, "text"
    return _Artifact(
        priority,
        language_priority,
        relative,
        extractor,
        kind,
        content,
        title,
        metadata,
    )


def _read_json_evidence(
    raw: str,
) -> tuple[str, str | None, Mapping[str, object] | None]:
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return "", None, None
    if not isinstance(data, dict):
        return "", None, None
    content = ""
    for key in ("transcript", "description", "caption", "content", "text", "summary"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            content = value.strip()
            break
    title_value = data.get("title")
    title = title_value.strip() if isinstance(title_value, str) else None
    safe_metadata: dict[str, object] = {}
    evidence_keys = {
        "caption",
        "content",
        "description",
        "summary",
        "text",
        "transcript",
    }
    for key, value in data.items():
        if key in evidence_keys:
            continue
        if (
            isinstance(value, str)
            and len(value) <= 500
            or isinstance(value, bool | int | float)
            or value is None
        ):
            safe_metadata[key] = value
        if len(safe_metadata) >= 24:
            break
    return content, title, safe_metadata


def _read_html_evidence(raw: str) -> tuple[str, str | None]:
    parser = _VisibleHtmlParser()
    try:
        parser.feed(raw)
        parser.close()
    except Exception:
        return "", None
    content = _collapse_whitespace(" ".join(parser.text))
    title = _collapse_whitespace(" ".join(parser.title)) or None
    return content, title


def _parse_subtitle(raw: str) -> str:
    lines: list[str] = []
    for line in raw.splitlines():
        candidate = line.strip()
        if (
            not candidate
            or candidate.isdecimal()
            or "-->" in candidate
            or candidate == "WEBVTT"
            or candidate.startswith(("Kind:", "Language:", "NOTE", "STYLE", "REGION"))
        ):
            continue
        visible = _strip_html_tags(candidate)
        if visible and (not lines or lines[-1] != visible):
            lines.append(visible)
    return _collapse_whitespace(" ".join(lines))


def _language_priority(filename: str) -> int:
    lower = filename.lower()
    tokens = set(re.split(r"[^a-z0-9]+", lower))
    if "ko" in tokens or "kor" in tokens:
        return 0
    if "en" in tokens or "eng" in tokens:
        return 1
    return 2


def _source_type(extractor: str) -> str:
    if extractor == "ytdlp":
        return "video"
    if extractor == "gallerydl":
        return "social"
    if extractor in {"defuddle", "mercury", "readability", "trafilatura"}:
        return "article"
    return "web"


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
        else:
            self.text.append(data)


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


def _remaining(deadline: float, clock: Callable[[], float]) -> float:
    remaining = deadline - clock()
    if remaining <= 0:
        raise SourceError("timeout")
    return remaining


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
