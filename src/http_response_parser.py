"""HTTP response parser — raw HTTP bytes to structured result (Sub-AC 15.1c).

Parses raw HTTP response text/bytes into a structured ``HttpResponse`` with
status code, headers, content-type, and deserialized body.  Supports JSON,
XML, plain text, and opaque binary bodies.

Designed as an independently runnable module — no network, filesystem,
or CLI dependencies.  Pure function of ``(raw_response: str | bytes)``
→ ``HttpResponse``.

Usage::

    from src.http_response_parser import parse_http_response

    raw = (
        b"HTTP/1.1 200 OK\\r\\n"
        b"Content-Type: application/json\\r\\n"
        b"\\r\\n"
        b'{"status": "ok", "count": 42}'
    )
    resp = parse_http_response(raw)
    assert resp.status_code == 200
    assert resp.body_parsed == {"status": "ok", "count": 42}

Status-Line extraction
----------------------
Parses ``HTTP/1.x <code> <reason>``, extracting the numeric status code
and textual reason phrase.  HTTP/2 and HTTP/3 pseudo-status headers are
also recognised (``:status: 200``).

Header parsing
--------------
RFC 7230 §3.2 compliant header-line parser:
* Leading / trailing OWS stripped from field-values.
* Multi-line folded headers (obsolete RFC 7230 §3.2.2) are unfolded.
* Duplicate headers (e.g. ``Set-Cookie``) are joined with ``", "``.

Content-Type detection
----------------------
Extracts MIME type and optional parameters (``charset``, ``boundary``)
from the ``Content-Type`` header.
Falls back to ``application/octet-stream`` when absent.

Body deserialization strategies
-------------------------------
* ``application/json`` (or ``.../+json``) — ``json.loads(body)`` → dict/list.
* ``application/xml``, ``text/xml`` (or ``.../+xml``) — ``ET.fromstring(body)``.
* ``text/*`` — decode with detected charset (default UTF-8) → str.
* ``application/octet-stream`` / unknown — raw bytes preserved;
  a lightweight heuristic still attempts JSON parse for common API patterns.

Design principles
-----------------
* Pure function — no I/O, no side effects.
* Defensive — each parser has its own try/except; best-effort extraction.
* Immutable result — ``HttpResponse`` is a frozen dataclass.
* Parsing metadata — ``parser_errors`` list captures non-fatal parse issues.
* Follows the same dataclass pattern as ``http_request_builder`` and
  ``action_descriptor_validator``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Mapping
from xml.etree import ElementTree as ET


# ── Constants ──────────────────────────────────────────────────────────

_HTTP_STATUS_LINE_RE: re.Pattern[str] = re.compile(
    r"^HTTP/\d\.\d\s+(\d{3})\s+(.*)",
    re.IGNORECASE,
)
"""Regex for ``HTTP/1.x <code> <reason>`` status lines."""

_HTTP2_STATUS_RE: re.Pattern[str] = re.compile(
    r"^:status:\s*(\d{3})",
    re.IGNORECASE,
)
"""Regex for HTTP/2 / HTTP/3 pseudo-header ``:status: <code>`` lines."""

_CHARSET_RE: re.Pattern[str] = re.compile(
    r"charset\s*=\s*([^\s;,\"]+)",
    re.IGNORECASE,
)
"""Extract charset value from Content-Type parameter list."""


# ── Data types ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class HttpResponse:
    """A fully-parsed HTTP response.

    Attributes:
        status_code: Numeric HTTP status code (e.g. 200).
            -1 when the status line is unparseable.
        status_text: Reason phrase from the status line
            (e.g. ``"OK"``, ``"Not Found"``).
            Empty string when unparseable.
        headers: Case-insensitive-ish HTTP headers dict
            (keys stored as-provided; lookups are case-insensitive
            via the ``get_header`` helper).
        body_raw: Original body bytes (without transfers like chunked
            encoding applied — callers should dechunk before passing
            to this parser).  Empty ``b""`` when no body.
        content_type: MIME type extracted from ``Content-Type``
            header, without parameters (e.g. ``"application/json"``).
            ``None`` when the header is absent.
        content_type_params: Dict of parsed Content-Type parameters
            (e.g. ``{"charset": "utf-8", "boundary": "abc"}``).
        body_parsed: Deserialized body — ``dict`` / ``list`` for JSON,
            ``ET.Element`` for XML, ``str`` for text, ``bytes``
            for unrecognised / opaque content.  ``None`` when empty.
        body_format: The deserialization strategy that produced
            ``body_parsed``: ``"json"``, ``"xml"``, ``"text"``,
            ``"bytes"``, or ``"none"``.
        parser_errors: Non-fatal parse warnings (e.g. JSON decode
            failure on content that looked like it should be JSON).
        total_bytes: Total raw bytes consumed from the input (useful
            for verifying that the entire response was consumed).
    """

    status_code: int
    status_text: str
    headers: Mapping[str, str]
    body_raw: bytes
    content_type: str | None
    content_type_params: Mapping[str, str] = field(default_factory=dict)
    body_parsed: Any = None
    body_format: str = "none"
    parser_errors: list[str] = field(default_factory=list)
    total_bytes: int = 0

    def get_header(self, name: str) -> str | None:
        """Case-insensitive header lookup.

        Args:
            name: Header name (case-insensitive).

        Returns:
            Header value or ``None``.
        """
        lower = name.lower()
        for k, v in self.headers.items():
            if k.lower() == lower:
                return v
        return None

    @property
    def is_success(self) -> bool:
        """True for 2xx status codes."""
        return 200 <= self.status_code < 300

    @property
    def is_redirect(self) -> bool:
        """True for 3xx status codes."""
        return 300 <= self.status_code < 400

    @property
    def is_client_error(self) -> bool:
        """True for 4xx status codes."""
        return 400 <= self.status_code < 500

    @property
    def is_server_error(self) -> bool:
        """True for 5xx status codes."""
        return 500 <= self.status_code < 600


# ── Status-Line parsing ────────────────────────────────────────────────


def _parse_status_line(first_line: str) -> tuple[int, str]:
    """Extract status code and reason phrase from the status line.

    Handles:
    * ``HTTP/1.x <code> <reason>``
    * ``:status: <code>`` (HTTP/2 pseudo-header)

    Args:
        first_line: First line of the HTTP response (stripped).

    Returns:
        ``(status_code, reason_phrase)`` tuple.
        ``(-1, "")`` on failure.
    """
    # HTTP/1.x style
    m = _HTTP_STATUS_LINE_RE.match(first_line)
    if m:
        return int(m.group(1)), m.group(2).strip()

    # HTTP/2+ pseudo-header
    m = _HTTP2_STATUS_RE.match(first_line)
    if m:
        code = int(m.group(1))
        return code, ""

    return -1, ""


# ── Header parsing ─────────────────────────────────────────────────────


def _unfold_header_lines(raw_lines: list[str]) -> list[str]:
    """Unfold multi-line (obsolete) folded headers per RFC 7230 §3.2.2.

    A continuation line starts with at least one SP or HTAB.
    We collapse it onto the previous line, inserting a single SP.

    Args:
        raw_lines: Stripped header lines (no trailing CR).

    Returns:
        Unfolded header lines.
    """
    unfolded: list[str] = []
    for line in raw_lines:
        if line and line[0] in (" ", "\t"):
            if unfolded:
                # Collapse continuation — trim leading whitespace,
                # replace with single SP
                unfolded[-1] = unfolded[-1].rstrip() + " " + line.lstrip()
            # else: orphan continuation — drop silently
        else:
            unfolded.append(line)
    return unfolded


def _parse_headers(header_lines: list[str]) -> dict[str, str]:
    """Parse RFC 7230 header lines into a key-value dict.

    Rules:
    * ``Name: Value`` format.  Lines without a colon are skipped.
    * Leading / trailing OWS in value is stripped.
    * Duplicate names are joined with ``", "`` (common for
      ``Set-Cookie``, ``Warning``, etc.).

    Args:
        header_lines: Unfolded header lines (one per logical header).

    Returns:
        Dict of ``{name: value}``.  Keys preserve original casing.
    """
    headers: dict[str, str] = {}
    for line in header_lines:
        if ":" not in line:
            continue
        name, _, value = line.partition(":")
        value = value.strip()
        if name in headers:
            headers[name] = headers[name] + ", " + value
        else:
            headers[name] = value
    return headers


# ── Content-Type detection ─────────────────────────────────────────────


def _parse_content_type(
    headers: Mapping[str, str],
) -> tuple[str | None, dict[str, str]]:
    """Extract media type and parameters from the Content-Type header.

    Args:
        headers: Parsed HTTP headers dict.

    Returns:
        ``(media_type, params)`` tuple.  *media_type* is the lowercased
        MIME type without parameters; *params* is a dict of key-value
        parameters (lowercased keys, unquoted values).  Both are empty
        / ``None`` when the header is absent.
    """
    raw = None
    for k, v in headers.items():
        if k.lower() == "content-type":
            raw = v
            break

    if not raw:
        return None, {}

    # Split off parameters
    parts = raw.split(";", 1)
    media_type = parts[0].strip().lower()
    params: dict[str, str] = {}

    if len(parts) > 1:
        # Parse parameter list: key=value pairs
        param_str = parts[1]
        for param in _split_params(param_str):
            if "=" in param:
                pk, _, pv = param.partition("=")
                pk = pk.strip().lower()
                pv = pv.strip().strip('"').strip("'")
                params[pk] = pv
            else:
                pk = param.strip().lower()
                if pk:
                    params[pk] = ""

    return media_type, params


def _split_params(param_str: str) -> list[str]:
    """Split a Content-Type parameter string respecting quoted values.

    Handles ``text/html; charset=utf-8; boundary="abc def"`` by
    splitting on semicolons that are NOT inside quotes.

    Args:
        param_str: Parameter substring (after the first ``;``).

    Returns:
        List of individual parameter strings.
    """
    params: list[str] = []
    current: list[str] = []
    in_quote = False
    quote_char = ""

    for ch in param_str:
        if ch in ('"', "'") and not in_quote:
            in_quote = True
            quote_char = ch
        elif ch == quote_char and in_quote:
            in_quote = False
            quote_char = ""
        elif ch == ";" and not in_quote:
            params.append("".join(current))
            current = []
            continue
        current.append(ch)

    remaining = "".join(current).strip()
    if remaining:
        params.append(remaining)

    return params


# ── Charset extraction ─────────────────────────────────────────────────


def _detect_charset(
    content_type: str | None,
    content_type_params: Mapping[str, str],
    body: bytes,
) -> str:
    """Determine the charset for body decoding.

    Priority:
    1. ``charset`` parameter from Content-Type.
    2. BOM-based detection (UTF-8/16/32).
    3. ``Content-Type: application/json`` → UTF-8 (RFC 8259).
    4. Default ``utf-8``.

    Args:
        content_type: Media type string or ``None``.
        content_type_params: Parsed Content-Type parameters.
        body: Raw body bytes.

    Returns:
        Encoding name suitable for ``bytes.decode()``.
    """
    # 1. Explicit charset parameter
    if "charset" in content_type_params:
        charset = content_type_params["charset"]
        try:
            "test".encode(charset)
            return charset
        except (LookupError, UnicodeEncodeError):
            pass

    # 2. BOM detection
    if body.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    if body.startswith(b"\xff\xfe"):
        return "utf-16-le"
    if body.startswith(b"\xfe\xff"):
        return "utf-16-be"

    # 3. JSON defaults to UTF-8 per RFC 8259
    if content_type and ("json" in content_type or content_type.endswith("+json")):
        return "utf-8"

    return "utf-8"


# ── Body deserialization strategies ────────────────────────────────────


def _deserialize_json(
    body: bytes,
    charset: str,
    errors: list[str],
) -> tuple[Any, list[str]]:
    """Attempt JSON deserialization.

    Args:
        body: Raw body bytes.
        charset: Encoding for decoding.
        errors: Mutable error list (appended to on failure).

    Returns:
        ``(parsed, errors)`` where *parsed* is the Python object
        or ``None`` on failure.
    """
    try:
        text = body.decode(charset)
    except UnicodeDecodeError:
        text = body.decode(charset, errors="replace")
    try:
        return json.loads(text), errors
    except json.JSONDecodeError as exc:
        errors.append(f"JSON parse error: {exc}")
        return None, errors


def _deserialize_xml(
    body: bytes,
    charset: str,
    errors: list[str],
) -> tuple[Any, list[str]]:
    """Attempt XML deserialization via ElementTree.

    Args:
        body: Raw body bytes.
        charset: Encoding for decoding.
        errors: Mutable error list.

    Returns:
        ``(ET.Element | None, errors)``.
    """
    try:
        return ET.fromstring(body.decode(charset)), errors
    except (ET.ParseError, UnicodeDecodeError) as exc:
        errors.append(f"XML parse error: {exc}")
        return None, errors


def _deserialize_text(
    body: bytes,
    charset: str,
    errors: list[str],
) -> tuple[str | None, list[str]]:
    """Decode body as plain text.

    Returns:
        ``(str, errors)`` — always succeeds (fallback to replace).
    """
    try:
        return body.decode(charset), errors
    except UnicodeDecodeError as exc:
        errors.append(f"Text decode warning (used replacement): {exc}")
        return body.decode(charset, errors="replace"), errors


# ── Main public API ────────────────────────────────────────────────────


def parse_http_response(raw_response: str | bytes) -> HttpResponse:
    """Parse a raw HTTP response into a structured ``HttpResponse``.

    Accepts the complete HTTP response string (status line + headers
    + body) and returns an immutable ``HttpResponse`` with all fields
    extracted and body deserialized according to Content-Type.

    Args:
        raw_response: Raw HTTP response as ``str`` or ``bytes``.
            Must include the full response — status line, headers,
            blank line separator, and body.

    Returns:
        ``HttpResponse`` dataclass instance.

    Raises:
        TypeError: If *raw_response* is not ``str`` or ``bytes``.

    Examples:
        Minimal JSON response::

            >>> raw = b"HTTP/1.1 200 OK\\r\\nContent-Type: application/json\\r\\n\\r\\n{\\"ok\\":true}"
            >>> resp = parse_http_response(raw)
            >>> resp.status_code
            200
            >>> resp.body_parsed
            {'ok': True}
            >>> resp.body_format
            'json'

        Text response with charset::

            >>> raw = (
            ...     b"HTTP/1.1 200 OK\\r\\n"
            ...     b"Content-Type: text/plain; charset=iso-8859-1\\r\\n"
            ...     b"\\r\\n"
            ...     b"Hello World"
            ... )
            >>> resp = parse_http_response(raw)
            >>> resp.content_type
            'text/plain'
            >>> resp.content_type_params['charset']
            'iso-8859-1'
            >>> resp.body_parsed
            'Hello World'

        Error response with XML body::

            >>> raw = (
            ...     b"HTTP/1.1 404 Not Found\\r\\n"
            ...     b"Content-Type: application/xml\\r\\n"
            ...     b"\\r\\n"
            ...     b"<error><code>404</code><msg>Not Found</msg></error>"
            ... )
            >>> resp = parse_http_response(raw)
            >>> resp.status_code
            404
            >>> resp.is_client_error
            True
            >>> resp.body_format
            'xml'
    """
    if isinstance(raw_response, str):
        raw_bytes = raw_response.encode("utf-8", errors="replace")
    elif isinstance(raw_response, bytes):
        raw_bytes = raw_response
    else:
        raise TypeError(
            f"raw_response must be str or bytes, got {type(raw_response).__name__}"
        )

    parse_errors: list[str] = []
    total_bytes = len(raw_bytes)

    # ── Split on the first double-CRLF (or double-LF) at BYTE level ────
    # RFC 7230 §3: header section ends at first empty line.
    # We work with bytes so binary body content survives untouched.
    double_crlf = b"\r\n\r\n"
    double_lf = b"\n\n"

    if double_crlf in raw_bytes:
        crlf = b"\r\n"
        header_bytes, _, body_bytes = raw_bytes.partition(double_crlf)
    elif double_lf in raw_bytes:
        crlf = b"\n"
        header_bytes, _, body_bytes = raw_bytes.partition(double_lf)
    else:
        # No body — entire response is headers only
        crlf = b"\n"
        header_bytes = raw_bytes
        body_bytes = b""

    # Decode header section as Latin-1 (safe round-trip for any byte value).
    # HTTP headers are ASCII per RFC 7230 but Latin-1 preserves every byte
    # for lenient parsing of non-ASCII headers.
    header_section = header_bytes.decode("latin-1")

    # ── Parse status line ──────────────────────────────────────────────
    header_lines_raw = header_section.split(crlf.decode("latin-1"))
    if not header_lines_raw:
        status_code, status_text = -1, ""
    else:
        status_code, status_text = _parse_status_line(header_lines_raw[0].strip())
        header_lines_raw = header_lines_raw[1:]  # rest are headers

    # ── Unfold and parse headers ───────────────────────────────────────
    unfolded = _unfold_header_lines([h.strip() for h in header_lines_raw])
    headers = _parse_headers(unfolded)

    # ── Content-Type detection ─────────────────────────────────────────
    content_type, content_type_params = _parse_content_type(headers)

    # ── Charset ────────────────────────────────────────────────────────
    charset = _detect_charset(content_type, content_type_params, body_bytes)

    # ── Deserialize body ───────────────────────────────────────────────
    body_parsed: Any = None
    body_format = "none"

    if not body_bytes:
        body_parsed = None
        body_format = "none"
    elif content_type is None:
        # Best-effort: try JSON first (common API pattern), fall back
        parsed, parse_errors = _deserialize_json(body_bytes, charset, parse_errors)
        if parsed is not None:
            body_parsed = parsed
            body_format = "json"
        elif body_bytes:
            body_parsed, parse_errors = _deserialize_text(body_bytes, charset, parse_errors)
            body_format = "text"
    elif (
        content_type == "application/json"
        or content_type.endswith("+json")
        or content_type.startswith("application/json")
    ):
        parsed, parse_errors = _deserialize_json(body_bytes, charset, parse_errors)
        if parsed is not None:
            body_parsed = parsed
            body_format = "json"
        else:
            # JSON parse failed despite correct content-type — keep raw
            body_parsed, parse_errors = _deserialize_text(body_bytes, charset, parse_errors)
            body_format = "text"
    elif (
        content_type in ("application/xml", "text/xml")
        or content_type.endswith("+xml")
    ):
        parsed, parse_errors = _deserialize_xml(body_bytes, charset, parse_errors)
        if parsed is not None:
            body_parsed = parsed
            body_format = "xml"
        else:
            # XML parse failed — fall back to text
            body_parsed, parse_errors = _deserialize_text(body_bytes, charset, parse_errors)
            body_format = "text"
    elif content_type.startswith("text/"):
        body_parsed, parse_errors = _deserialize_text(body_bytes, charset, parse_errors)
        body_format = "text"
    else:
        # Unknown or octet-stream — keep as bytes
        # But still attempt a lightweight JSON heuristic for common APIs
        if body_bytes and body_bytes[0:1] in (b"{", b"["):
            parsed, parse_errors = _deserialize_json(body_bytes, charset, parse_errors)
            if parsed is not None:
                body_parsed = parsed
                body_format = "json"
            else:
                body_parsed = body_bytes
                body_format = "bytes"
        else:
            body_parsed = body_bytes
            body_format = "bytes"

    return HttpResponse(
        status_code=status_code,
        status_text=status_text,
        headers=headers,
        body_raw=body_bytes,
        content_type=content_type,
        content_type_params=content_type_params,
        body_parsed=body_parsed,
        body_format=body_format,
        parser_errors=parse_errors,
        total_bytes=total_bytes,
    )
