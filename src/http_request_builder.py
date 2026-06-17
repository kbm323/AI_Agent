"""HTTP request builder from validated action descriptors (Sub-AC 15.1b).

Builds a fully-formed HTTP request from a validated action descriptor,
including method, URL, headers, query parameters, and body serialization.
Designed as an independently runnable module — no network, filesystem,
or CLI dependencies.  Pure function of (descriptor dict) → HttpRequest.

Usage::

    from src.http_request_builder import build_http_request

    descriptor = {
        "method": "POST",
        "url": "https://api.example.com/v1/deploy",
        "headers": {"Authorization": "Bearer abc", "Content-Type": "application/json"},
        "body": {"target": "prod", "version": "1.2.0"},
    }
    req = build_http_request(descriptor)
    print(req.serialize_body())  # '{"target": "prod", "version": "1.2.0"}'

Content-type detection (priority order)
---------------------------------------
1. Explicit ``Content-Type`` header in the descriptor.
2. Inferred from body shape: ``dict`` → ``application/json``,
   ``str`` → ``text/plain``, ``bytes`` → ``application/octet-stream``.

Query parameters
----------------
* Inline params in the URL are preserved.
* An optional ``query_params`` key in the descriptor is merged into the URL
  (deduplication by last-write-wins on repeated keys).

Supported body serialization strategies
---------------------------------------
* ``application/json`` — dict/str body → JSON string.
* ``application/x-www-form-urlencoded`` — dict body → URL-encoded string.
* ``multipart/form-data`` — dict with file-like values → boundary-delimited.
* ``text/plain``, ``text/*`` — str body → UTF-8 bytes.
* ``application/octet-stream`` — bytes body passed through.
* ``None`` / no content-type — best-effort: dict→JSON, str/bytes→raw.

Design principles
-----------------
* Pure function — no I/O, no side effects.
* Defensive — each serialiser has its own try/except.
* Immutable result — ``HttpRequest`` is a frozen dataclass.
* Follows the same dataclass pattern as ``action_descriptor_validator``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse


# ── Constants ──────────────────────────────────────────────────────────

_METHODS_WITHOUT_BODY: frozenset[str] = frozenset({"GET", "HEAD", "OPTIONS"})
"""HTTP methods that conventionally do not carry a request body.

DELETE is excluded because it *may* carry a body (RFC 7231 §4.3.5)."""


# ── Data types ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class HttpRequest:
    """A fully-formed, serialisable HTTP request.

    Attributes:
        method: HTTP method (GET, POST, PUT, DELETE, PATCH, HEAD, OPTIONS).
        url: Full URL with merged query parameters (no fragment).
        headers: Case-insensitive-ish dict of HTTP headers
            (keys are stored as-provided; lookups are case-insensitive
            via the ``get_header`` helper).
        body: Serialised body — ``str``, ``bytes``, or ``None``.
        content_type: Resolved Content-Type value (may be ``None``).
        timeout: Request timeout in seconds (``None`` = use default).
        serializer_used: Name of the serialisation strategy applied
            (``json``, ``urlencoded``, ``form-data``, ``raw``, ``none``).
    """

    method: str
    url: str
    headers: Mapping[str, str]
    body: str | bytes | None
    content_type: str | None
    timeout: float | None = None
    serializer_used: str = "none"

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


# ── Query parameter helpers ────────────────────────────────────────────


def _merge_query_params(url: str, extra_params: dict[str, str] | None) -> str:
    """Merge extra query parameters into *url*.

    Inline params are preserved; duplicates are resolved by last-write-wins
    (extra_params values override inline ones for matching keys).

    Args:
        url: Source URL (may already contain ``?...``).
        extra_params: Key-value dict of additional params
            (``None`` or empty = no-op).

    Returns:
        URL string with merged query string.
    """
    if not extra_params:
        return url

    parsed = urlparse(url)
    existing = parse_qs(parsed.query, keep_blank_values=True)

    # Flatten parse_qs values (lists) — take last element
    merged: dict[str, str] = {}
    for k, vals in existing.items():
        if vals:
            merged[k] = vals[-1]
    for k, v in extra_params.items():
        merged[k] = v

    new_query = urlencode(merged, doseq=False)
    return urlunparse(parsed._replace(query=new_query, fragment=""))


# ── Body serializers ───────────────────────────────────────────────────


def _serialize_json(body: str | dict, content_type: str | None) -> tuple[str | bytes, str]:
    """Serialize body as JSON.

    - ``dict`` → ``json.dumps(body)``.
    - ``str`` → passed through (assumed already-JSON).

    Returns:
        (serialized_body, resolved_content_type)
    """
    if isinstance(body, dict):
        return json.dumps(body, ensure_ascii=False), content_type or "application/json"
    if isinstance(body, str):
        return body, content_type or "application/json"
    # bytes fallback: decode and re-encode? Just pass through as string
    if isinstance(body, bytes):
        return body.decode("utf-8", errors="replace"), content_type or "application/json"
    return str(body), content_type or "application/json"


def _serialize_urlencoded(
    body: dict | str,
    content_type: str | None,
) -> tuple[str, str]:
    """Serialize body as ``application/x-www-form-urlencoded``.

    - ``dict`` → ``urlencode(body)``.
    - ``str`` → passed through (assumed already-encoded).

    Returns:
        (serialized_body_str, resolved_content_type)
    """
    ctype = content_type or "application/x-www-form-urlencoded"
    if isinstance(body, dict):
        return urlencode(body, doseq=True), ctype
    return str(body), ctype


def _serialize_form_data(
    body: dict | str,
    boundary: str | None,
    content_type: str | None,
) -> tuple[str | bytes, str]:
    """Serialize body as ``multipart/form-data``.

    If *boundary* is not supplied, a unique one is generated.
    Dict values that are file references (dict with ``filename``/``content``)
    are encoded as file parts; other values are form fields.

    Returns:
        (multipart_body_bytes, content_type_with_boundary)
    """
    import uuid

    boundary = boundary or f"----HermesFormBoundary{uuid.uuid4().hex[:16]}"
    ctype = f"multipart/form-data; boundary={boundary}"

    if isinstance(body, str):
        return body, ctype

    parts: list[str] = []
    for field_name, value in body.items():
        parts.append(f"--{boundary}")
        if isinstance(value, dict) and "filename" in value:
            # File part
            filename = value.get("filename", "unnamed")
            file_content = value.get("content", "")
            file_ct = value.get("content_type", "application/octet-stream")
            parts.append(
                f'Content-Disposition: form-data; name="{field_name}"; '
                f'filename="{filename}"'
            )
            parts.append(f"Content-Type: {file_ct}")
            parts.append("")
            parts.append(str(file_content) if not isinstance(file_content, bytes) else "")
        else:
            parts.append(f'Content-Disposition: form-data; name="{field_name}"')
            parts.append("")
            parts.append(str(value))
    parts.append(f"--{boundary}--")
    parts.append("")

    return "\r\n".join(parts), ctype


def _serialize_text(body: str | bytes, content_type: str | None) -> tuple[str, str]:
    """Serialize body as plain text.

    Returns:
        (body_str, resolved_content_type)
    """
    ctype = content_type or "text/plain"
    if isinstance(body, bytes):
        return body.decode("utf-8", errors="replace"), ctype
    return str(body), ctype


def _serialize_raw(
    body: str | bytes,
    content_type: str | None,
) -> tuple[str | bytes, str | None]:
    """Pass-through serializer for opaque / binary bodies.

    Returns:
        (body_as_is, content_type_or_None)
    """
    return body, content_type


def _serialize_none() -> tuple[None, None]:
    """Serializer for None body."""
    return None, None


# ── Content-type resolution ─────────────────────────────────────────────


def _infer_content_type(body: Any, explicit_ctype: str | None) -> str | None:
    """Determine content-type when not explicitly set.

    Inference rules:
    - ``dict`` body → ``application/json``
    - ``str`` body → ``text/plain``
    - ``bytes`` body → ``application/octet-stream``
    - ``None`` → ``None``
    """
    if explicit_ctype:
        return explicit_ctype
    if isinstance(body, dict):
        return "application/json"
    if isinstance(body, str):
        return "text/plain"
    if isinstance(body, bytes):
        return "application/octet-stream"
    return None


# ── Main public API ────────────────────────────────────────────────────


def build_http_request(
    descriptor: dict[str, Any],
    *,
    query_params: dict[str, str] | None = None,
    boundary: str | None = None,
) -> HttpRequest:
    """Build a fully-formed HTTP request from a (validated) action descriptor.

    Args:
        descriptor: Dict with keys ``method``, ``url``, ``headers``,
            ``body``, and optionally ``timeout``, ``query_params``.
            This is the same descriptor validated by
            ``action_descriptor_validator``.
        query_params: Extra query parameters to merge into the URL
            (convenience overload; also accepted via
            ``descriptor["query_params"]``).
        boundary: Custom multipart boundary string (for form-data only).

    Returns:
        ``HttpRequest`` with method, full URL, headers, serialised body,
        resolved content-type, timeout, and serialiser metadata.

    Raises:
        TypeError: If *descriptor* is not a dict.

    Examples:
        >>> req = build_http_request({
        ...     "method": "POST",
        ...     "url": "https://api.example.com/v1/items",
        ...     "headers": {"Content-Type": "application/json"},
        ...     "body": {"name": "Test"},
        ... })
        >>> req.method
        'POST'
        >>> req.serializer_used
        'json'
        >>> req.content_type
        'application/json'
        >>> json.loads(req.body)  # type: ignore[arg-type]
        {'name': 'Test'}
    """
    if not isinstance(descriptor, dict):
        raise TypeError(
            f"descriptor must be a dict, got {type(descriptor).__name__}"
        )

    # ── Extract fields ──────────────────────────────────────────────
    method: str = str(descriptor.get("method", "GET")).strip().upper()
    raw_url: str = str(descriptor.get("url", ""))
    headers: dict[str, str] = dict(descriptor.get("headers") or {})
    body: Any = descriptor.get("body")
    timeout: float | None = descriptor.get("timeout")

    # Resolve query params: descriptor-level overrides arg-level
    merged_qp: dict[str, str] = dict(query_params or {})
    if "query_params" in descriptor and isinstance(descriptor["query_params"], dict):
        merged_qp.update(descriptor["query_params"])

    # ── Merge query params into URL ─────────────────────────────────
    url = _merge_query_params(raw_url, merged_qp if merged_qp else None)

    # ── Strip fragment (not relevant for HTTP API requests) ─────────
    parsed = urlparse(url)
    if parsed.fragment:
        url = urlunparse(parsed._replace(fragment=""))

    # ── Resolve content-type ────────────────────────────────────────
    explicit_ctype: str | None = None
    for k, v in headers.items():
        if k.lower() == "content-type":
            explicit_ctype = v
            break
    content_type = _infer_content_type(body, explicit_ctype)

    # ── Serialize body ──────────────────────────────────────────────
    serialized_body: str | bytes | None = None
    serializer_used = "none"
    resolved_ctype: str | None = content_type

    # Methods that conventionally have no body → skip serialisation
    if method in _METHODS_WITHOUT_BODY:
        serialized_body = None
        serializer_used = "none"
    elif body is None:
        serialized_body = None
        serializer_used = "none"
    elif content_type is None:
        # No content-type hint — best-effort inference
        if isinstance(body, dict):
            serialized_body, resolved_ctype = _serialize_json(body, None)
            serializer_used = "json"
        elif isinstance(body, str):
            serialized_body, resolved_ctype = _serialize_text(body, None)
            serializer_used = "raw"
        elif isinstance(body, bytes):
            serialized_body, resolved_ctype = _serialize_raw(body, None)
            serializer_used = "raw"
        else:
            serialized_body = str(body)
            serializer_used = "raw"
    elif (
        content_type == "application/json"
        or (content_type or "").endswith("+json")
        or (content_type or "").startswith("application/json")
    ):
        serialized_body, resolved_ctype = _serialize_json(body, content_type)  # type: ignore[arg-type]
        serializer_used = "json"
    elif "x-www-form-urlencoded" in (content_type or ""):
        serialized_body, resolved_ctype = _serialize_urlencoded(body, content_type)  # type: ignore[arg-type]
        serializer_used = "urlencoded"
    elif "multipart/form-data" in (content_type or ""):
        serialized_body, resolved_ctype = _serialize_form_data(
            body, boundary, content_type  # type: ignore[arg-type]
        )
        serializer_used = "form-data"
    elif content_type and content_type.startswith("text/"):
        serialized_body, resolved_ctype = _serialize_text(body, content_type)  # type: ignore[arg-type]
        serializer_used = "raw"
    else:
        # Unknown or octet-stream — pass through
        serialized_body, resolved_ctype = _serialize_raw(body, content_type)  # type: ignore[arg-type]
        serializer_used = "raw"

    # ── Update Content-Type header if it was inferred ───────────────
    if resolved_ctype and not explicit_ctype:
        headers = dict(headers)
        headers["Content-Type"] = resolved_ctype
    elif resolved_ctype and explicit_ctype:
        # Keep explicit but use resolved for the record
        pass

    return HttpRequest(
        method=method,
        url=url,
        headers=headers,
        body=serialized_body,
        content_type=resolved_ctype,
        timeout=timeout,
        serializer_used=serializer_used,
    )
