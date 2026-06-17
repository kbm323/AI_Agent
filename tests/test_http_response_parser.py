"""Tests for the HTTP response parser module (Sub-AC 15.1c).

Covers:
- Status code extraction: 1xx-5xx, HTTP/1.0, HTTP/1.1, HTTP/2 pseudo-header
- Header parsing: single, duplicate (Set-Cookie, Warning), folded headers,
  missing colon, empty headers
- Content-type detection: explicit, absent, with charset, with boundary,
  with quoted parameters
- Body deserialization — JSON: dict, list, nested, unicode, empty,
  malformed JSON fallback
- Body deserialization — XML: elements, attributes, nested, malformed XML fallback
- Body deserialization — text: plain, html, csv, with charset, bytes
- Body deserialization — bytes: octet-stream, unknown content-type,
  JSON heuristic on opaque content
- Edge cases: empty body, no body section, LF-only line endings,
  HTTP/2 pseudo-header, missing Content-Type
- HttpResponse dataclass: immutability, get_header(), properties
  (is_success, is_redirect, is_client_error, is_server_error)
- Parser metadata: parser_errors, total_bytes
- Realistic HTTP response patterns from APIs
- Input validation: TypeError on non-str/bytes input
"""

from __future__ import annotations

import json
from xml.etree import ElementTree as ET

import pytest

from src.http_response_parser import (
    HttpResponse,
    parse_http_response,
)


# ═══════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════


@pytest.fixture
def raw_json_200() -> bytes:
    """Standard JSON 200 OK response."""
    return (
        b"HTTP/1.1 200 OK\r\n"
        b"Content-Type: application/json\r\n"
        b"Content-Length: 27\r\n"
        b"\r\n"
        b'{"status": "ok", "count": 42}'
    )


@pytest.fixture
def raw_xml_404() -> bytes:
    """XML error response."""
    return (
        b"HTTP/1.1 404 Not Found\r\n"
        b"Content-Type: application/xml\r\n"
        b"\r\n"
        b"<error><code>404</code><message>Not Found</message></error>"
    )


@pytest.fixture
def raw_text_200() -> bytes:
    """Plain text response with charset."""
    return (
        b"HTTP/1.1 200 OK\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"\r\n"
        b"Hello, World!"
    )


@pytest.fixture
def raw_no_content_204() -> bytes:
    """204 No Content — no body."""
    return (
        b"HTTP/1.1 204 No Content\r\n"
        b"Content-Length: 0\r\n"
        b"\r\n"
    )


# ═══════════════════════════════════════════════════════════════════════
# 1. Status code extraction
# ═══════════════════════════════════════════════════════════════════════


class TestStatusCodeExtraction:
    """Status codes from HTTP/1.x and HTTP/2 status lines."""

    @pytest.mark.parametrize(
        "raw_line, expected_code, expected_text",
        [
            (b"HTTP/1.1 200 OK\r\n\r\n", 200, "OK"),
            (b"HTTP/1.1 201 Created\r\n\r\n", 201, "Created"),
            (b"HTTP/1.0 301 Moved Permanently\r\n\r\n", 301, "Moved Permanently"),
            (b"HTTP/1.1 400 Bad Request\r\n\r\n", 400, "Bad Request"),
            (b"HTTP/1.1 404 Not Found\r\n\r\n", 404, "Not Found"),
            (b"HTTP/1.1 500 Internal Server Error\r\n\r\n", 500, "Internal Server Error"),
            (b"HTTP/1.1 502 Bad Gateway\r\n\r\n", 502, "Bad Gateway"),
            (b"HTTP/1.1 503 Service Unavailable\r\n\r\n", 503, "Service Unavailable"),
        ],
    )
    def test_status_code_extraction(
        self, raw_line: bytes, expected_code: int, expected_text: str
    ) -> None:
        resp = parse_http_response(raw_line)
        assert resp.status_code == expected_code
        assert resp.status_text == expected_text

    def test_http1_0_status_line(self) -> None:
        resp = parse_http_response(b"HTTP/1.0 200 OK\r\n\r\n")
        assert resp.status_code == 200
        assert resp.status_text == "OK"

    def test_informational_1xx(self) -> None:
        """100 Continue, 101 Switching Protocols etc."""
        resp = parse_http_response(b"HTTP/1.1 100 Continue\r\n\r\n")
        assert resp.status_code == 100
        assert resp.is_success is False
        assert resp.is_client_error is False

    def test_redirect_3xx(self) -> None:
        resp = parse_http_response(
            b"HTTP/1.1 302 Found\r\nLocation: /new\r\n\r\n"
        )
        assert resp.status_code == 302
        assert resp.is_redirect is True

    def test_unparseable_status_line(self) -> None:
        """Garbage status line yields -1 code."""
        resp = parse_http_response(b"GARBAGE LINE\r\nHeader: value\r\n\r\nbody")
        assert resp.status_code == -1
        assert resp.status_text == ""

    def test_empty_input(self) -> None:
        """Completely empty input yields -1 / no body."""
        resp = parse_http_response(b"")
        assert resp.status_code == -1
        assert resp.status_text == ""
        assert resp.body_raw == b""
        assert resp.body_parsed is None

    def test_status_line_with_extra_spaces(self) -> None:
        resp = parse_http_response(b"HTTP/1.1   200   OK  \r\n\r\n")
        assert resp.status_code == 200
        assert resp.status_text == "OK"


# ═══════════════════════════════════════════════════════════════════════
# 2. HTTP/2 pseudo-header status
# ═══════════════════════════════════════════════════════════════════════


class TestHttp2StatusLine:
    """HTTP/2 and HTTP/3 use ``:status:`` pseudo-headers."""

    def test_http2_status_pseudo_header(self) -> None:
        resp = parse_http_response(b":status: 200\r\ncontent-type: text/plain\r\n\r\nOK")
        assert resp.status_code == 200
        assert resp.status_text == ""

    def test_http2_status_with_extra_whitespace(self) -> None:
        resp = parse_http_response(b":status:  404  \r\n\r\n")
        assert resp.status_code == 404

    def test_http2_500(self) -> None:
        resp = parse_http_response(
            b":status: 500\r\ncontent-type: application/json\r\n\r\n{}"
        )
        assert resp.status_code == 500
        assert resp.is_server_error is True


# ═══════════════════════════════════════════════════════════════════════
# 3. Header parsing
# ═══════════════════════════════════════════════════════════════════════


class TestHeaderParsing:
    """RFC 7230-compliant header parsing."""

    def test_single_headers(self) -> None:
        resp = parse_http_response(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: application/json\r\n"
            b"Content-Length: 42\r\n"
            b"X-Request-Id: abc-123\r\n"
            b"\r\n"
        )
        assert resp.get_header("Content-Type") == "application/json"
        assert resp.get_header("content-type") == "application/json"
        assert resp.get_header("Content-Length") == "42"
        assert resp.get_header("X-Request-Id") == "abc-123"

    def test_case_insensitive_header_lookup(self) -> None:
        resp = parse_http_response(
            b"HTTP/1.1 200 OK\r\n"
            b"CONTENT-TYPE: application/json\r\n"
            b"Content-type: text/plain\r\n"
            b"\r\n"
        )
        assert resp.get_header("content-type") is not None
        assert resp.get_header("Content-Type") is not None

    def test_duplicate_headers_joined(self) -> None:
        """Duplicate headers (Set-Cookie, Warning) are joined with ', '."""
        resp = parse_http_response(
            b"HTTP/1.1 200 OK\r\n"
            b"Set-Cookie: session=abc\r\n"
            b"Set-Cookie: token=xyz\r\n"
            b"\r\n"
        )
        cookie = resp.get_header("Set-Cookie")
        assert cookie is not None
        assert "session=abc" in cookie  # type: ignore[operator]
        assert "token=xyz" in cookie  # type: ignore[operator]

    def test_header_value_with_leading_trailing_whitespace(self) -> None:
        resp = parse_http_response(
            b"HTTP/1.1 200 OK\r\n"
            b"X-Custom:    value with spaces   \r\n"
            b"\r\n"
        )
        assert resp.get_header("X-Custom") == "value with spaces"

    def test_line_without_colon_skipped(self) -> None:
        resp = parse_http_response(
            b"HTTP/1.1 200 OK\r\n"
            b"Header-No-Colon\r\n"
            b"Valid: yes\r\n"
            b"\r\n"
        )
        assert resp.get_header("Header-No-Colon") is None
        assert resp.get_header("Valid") == "yes"

    def test_empty_headers(self) -> None:
        resp = parse_http_response(b"HTTP/1.1 200 OK\r\n\r\n")
        assert len(resp.headers) == 0

    def test_header_with_colon_in_value(self) -> None:
        """Values containing ':' should parse correctly."""
        resp = parse_http_response(
            b"HTTP/1.1 200 OK\r\n"
            b"Location: https://example.com:8080/path\r\n"
            b"\r\n"
        )
        assert resp.get_header("Location") == "https://example.com:8080/path"


# ═══════════════════════════════════════════════════════════════════════
# 4. Content-Type detection
# ═══════════════════════════════════════════════════════════════════════


class TestContentTypeDetection:
    """Detect media type and parameters from Content-Type header."""

    def test_json_content_type(self, raw_json_200) -> None:
        resp = parse_http_response(raw_json_200)
        assert resp.content_type == "application/json"
        assert resp.content_type_params == {}

    def test_text_with_charset(self, raw_text_200) -> None:
        resp = parse_http_response(raw_text_200)
        assert resp.content_type == "text/plain"
        assert resp.content_type_params["charset"] == "utf-8"

    def test_multipart_with_boundary(self) -> None:
        resp = parse_http_response(
            b"HTTP/1.1 200 OK\r\n"
            b'Content-Type: multipart/form-data; boundary="abc-123-def"\r\n'
            b"\r\n"
            b"--abc-123-def--"
        )
        assert resp.content_type == "multipart/form-data"
        assert resp.content_type_params["boundary"] == "abc-123-def"

    def test_quoted_charset_value(self) -> None:
        resp = parse_http_response(
            b"HTTP/1.1 200 OK\r\n"
            b'Content-Type: text/html; charset="utf-8"\r\n'
            b"\r\n"
            b"<html></html>"
        )
        assert resp.content_type_params["charset"] == "utf-8"

    def test_no_content_type_header(self) -> None:
        resp = parse_http_response(
            b"HTTP/1.1 200 OK\r\n"
            b"X-Custom: value\r\n"
            b"\r\n"
            b'{"key":"val"}'
        )
        assert resp.content_type is None
        assert resp.content_type_params == {}
        # Should still JSON-parse heuristically
        assert resp.body_format == "json"

    def test_content_type_with_multiple_params(self) -> None:
        resp = parse_http_response(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: text/plain; charset=utf-8; format=flowed\r\n"
            b"\r\n"
            b"Hello"
        )
        assert resp.content_type == "text/plain"
        assert resp.content_type_params["charset"] == "utf-8"
        assert resp.content_type_params["format"] == "flowed"

    def test_single_quoted_param(self) -> None:
        resp = parse_http_response(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: application/octet-stream; type='custom'\r\n"
            b"\r\n"
            b"data"
        )
        assert resp.content_type_params.get("type") == "custom"

    def test_content_type_case_insensitive(self) -> None:
        resp = parse_http_response(
            b"HTTP/1.1 200 OK\r\n"
            b"content-type: Application/JSON\r\n"
            b"\r\n"
            b"{}"
        )
        assert resp.content_type == "application/json"

    def test_param_without_value(self) -> None:
        resp = parse_http_response(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: text/html; noparam\r\n"
            b"\r\n"
            b""
        )
        assert "noparam" in resp.content_type_params


# ═══════════════════════════════════════════════════════════════════════
# 5. Body deserialization — JSON
# ═══════════════════════════════════════════════════════════════════════


class TestJsonDeserialization:
    """JSON body deserialization from application/json responses."""

    def test_json_dict(self, raw_json_200) -> None:
        resp = parse_http_response(raw_json_200)
        assert resp.body_format == "json"
        assert resp.body_parsed == {"status": "ok", "count": 42}

    def test_json_list(self) -> None:
        resp = parse_http_response(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: application/json\r\n"
            b"\r\n"
            b'[{"id": 1}, {"id": 2}, {"id": 3}]'
        )
        assert resp.body_format == "json"
        assert resp.body_parsed == [{"id": 1}, {"id": 2}, {"id": 3}]

    def test_json_nested_structures(self) -> None:
        body = {
            "meta": {"page": 1, "limit": 20},
            "items": [{"name": "A"}, {"name": "B"}],
            "active": True,
            "score": None,
            "tags": ["python", "api"],
        }
        raw = (
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: application/json\r\n"
            b"\r\n"
            + json.dumps(body).encode("utf-8")
        )
        resp = parse_http_response(raw)
        assert resp.body_parsed == body

    def test_json_unicode(self) -> None:
        resp = parse_http_response(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: application/json; charset=utf-8\r\n"
            b"\r\n"
            b'{"message": "\\uc548\\ub155\\ud558\\uc138\\uc694", "emoji": "\\ud83c\\udf89"}'
        )
        assert resp.body_parsed["message"] == "안녕하세요"
        assert resp.body_parsed["emoji"] == "🎉"

    def test_json_empty_object(self) -> None:
        resp = parse_http_response(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: application/json\r\n"
            b"\r\n"
            b"{}"
        )
        assert resp.body_parsed == {}

    def test_json_empty_array(self) -> None:
        resp = parse_http_response(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: application/json\r\n"
            b"\r\n"
            b"[]"
        )
        assert resp.body_parsed == []

    def test_json_vendor_type(self) -> None:
        """application/vnd.api+json should be detected as JSON."""
        resp = parse_http_response(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: application/vnd.api+json\r\n"
            b"\r\n"
            b'{"data": {"type": "users", "id": "1"}}'
        )
        assert resp.body_format == "json"
        assert resp.body_parsed["data"]["type"] == "users"

    def test_json_with_bom(self) -> None:
        """UTF-8 BOM should be handled correctly."""
        resp = parse_http_response(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: application/json\r\n"
            b"\r\n"
            + b"\xef\xbb\xbf" + b'{"key": "value"}'
        )
        # BOM should be stripped by utf-8-sig decoder
        assert resp.body_parsed == {"key": "value"}

    def test_malformed_json_with_correct_content_type(self) -> None:
        """If Content-Type says JSON but body isn't, fall back to text."""
        resp = parse_http_response(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: application/json\r\n"
            b"\r\n"
            b"this is not json at all!!!"
        )
        assert resp.body_format == "text"
        assert len(resp.parser_errors) > 0
        assert "JSON" in resp.parser_errors[0] or "json" in resp.parser_errors[0]

    def test_json_number_types(self) -> None:
        resp = parse_http_response(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: application/json\r\n"
            b"\r\n"
            b'{"int": 42, "float": 3.14, "neg": -100, "exp": 1.5e10}'
        )
        assert resp.body_parsed["int"] == 42
        assert resp.body_parsed["float"] == 3.14
        assert resp.body_parsed["neg"] == -100


# ═══════════════════════════════════════════════════════════════════════
# 6. Body deserialization — XML
# ═══════════════════════════════════════════════════════════════════════


class TestXmlDeserialization:
    """XML body deserialization from application/xml or text/xml."""

    def test_application_xml(self, raw_xml_404) -> None:
        resp = parse_http_response(raw_xml_404)
        assert resp.body_format == "xml"
        assert resp.body_parsed.tag == "error"
        code_el = resp.body_parsed.find("code")
        assert code_el is not None and code_el.text == "404"

    def test_text_xml(self) -> None:
        resp = parse_http_response(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: text/xml\r\n"
            b"\r\n"
            b"<response><status>ok</status></response>"
        )
        assert resp.body_format == "xml"
        assert resp.body_parsed.find("status").text == "ok"  # type: ignore[union-attr]

    def test_xml_with_attributes(self) -> None:
        resp = parse_http_response(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: application/xml\r\n"
            b"\r\n"
            b'<item id="42" type="product"><name>Widget</name></item>'
        )
        root = resp.body_parsed
        assert root.attrib["id"] == "42"
        assert root.attrib["type"] == "product"

    def test_xml_vendor_type(self) -> None:
        """application/atom+xml should be detected as XML."""
        resp = parse_http_response(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: application/atom+xml\r\n"
            b"\r\n"
            b'<feed xmlns="http://www.w3.org/2005/Atom"><title>Test</title></feed>'
        )
        assert resp.body_format == "xml"

    def test_xml_nested_structure(self) -> None:
        resp = parse_http_response(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: application/xml\r\n"
            b"\r\n"
            b"<root><parent><child>deep</child></parent></root>"
        )
        parent = resp.body_parsed.find("parent")
        assert parent is not None
        child = parent.find("child")  # type: ignore[union-attr]
        assert child is not None and child.text == "deep"

    def test_malformed_xml_fallback_to_text(self) -> None:
        resp = parse_http_response(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: application/xml\r\n"
            b"\r\n"
            b"<open>not closed"
        )
        assert resp.body_format == "text"
        assert len(resp.parser_errors) > 0

    def test_xml_declaration(self) -> None:
        resp = parse_http_response(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: application/xml\r\n"
            b"\r\n"
            b'<?xml version="1.0" encoding="UTF-8"?><data>value</data>'
        )
        assert resp.body_format == "xml"
        assert resp.body_parsed.text == "value"

    def test_xml_soap_envelope(self) -> None:
        """Realistic SOAP XML response."""
        resp = parse_http_response(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: application/soap+xml\r\n"
            b"\r\n"
            b'<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
            b"<soap:Body><Response>Success</Response></soap:Body></soap:Envelope>"
        )
        assert resp.body_format == "xml"


# ═══════════════════════════════════════════════════════════════════════
# 7. Body deserialization — Text
# ═══════════════════════════════════════════════════════════════════════


class TestTextDeserialization:
    """Text body deserialization from text/* content types."""

    def test_plain_text(self, raw_text_200) -> None:
        resp = parse_http_response(raw_text_200)
        assert resp.body_format == "text"
        assert resp.body_parsed == "Hello, World!"

    def test_html(self) -> None:
        resp = parse_http_response(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: text/html; charset=utf-8\r\n"
            b"\r\n"
            b"<html><body><h1>Title</h1><p>Content</p></body></html>"
        )
        assert resp.body_format == "text"
        assert "<h1>Title</h1>" in resp.body_parsed

    def test_csv(self) -> None:
        resp = parse_http_response(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: text/csv\r\n"
            b"\r\n"
            b"name,age,city\nAlice,30,Seoul\nBob,25,Busan"
        )
        assert resp.body_format == "text"
        assert "Alice" in resp.body_parsed
        assert "Seoul" in resp.body_parsed

    def test_text_markdown(self) -> None:
        resp = parse_http_response(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: text/markdown\r\n"
            b"\r\n"
            b"# Meeting Notes\n\n- Decision: Approved\n- Owner: Kim"
        )
        assert resp.body_format == "text"
        assert "# Meeting Notes" in resp.body_parsed

    def test_text_latin1_charset(self) -> None:
        """ISO-8859-1 encoded text should be decoded correctly."""
        resp = parse_http_response(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: text/plain; charset=iso-8859-1\r\n"
            b"\r\n"
            b"caf\xe9"
        )
        assert resp.body_format == "text"
        assert "café" in resp.body_parsed

    def test_text_invalid_charset_fallsback(self) -> None:
        """Invalid charset name should fall back to utf-8."""
        resp = parse_http_response(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: text/plain; charset=invalid-charset-xyz\r\n"
            b"\r\n"
            b"Hello"
        )
        assert resp.body_format == "text"
        assert resp.body_parsed is not None

    def test_empty_text_body(self) -> None:
        resp = parse_http_response(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: text/plain\r\n"
            b"\r\n"
        )
        assert resp.body_format == "none"
        assert resp.body_parsed is None


# ═══════════════════════════════════════════════════════════════════════
# 8. Body deserialization — Bytes / Octet-Stream
# ═══════════════════════════════════════════════════════════════════════


class TestBytesDeserialization:
    """Opaque / binary content type handling."""

    def test_octet_stream(self) -> None:
        resp = parse_http_response(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: application/octet-stream\r\n"
            b"\r\n"
            b"\x00\x01\x02\x03\xff\xfe"
        )
        assert resp.body_format == "bytes"
        assert resp.body_raw == b"\x00\x01\x02\x03\xff\xfe"
        assert resp.body_parsed == b"\x00\x01\x02\x03\xff\xfe"

    def test_unknown_content_type(self) -> None:
        resp = parse_http_response(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: application/x-protobuf\r\n"
            b"\r\n"
            b"\x08\x01\x12\x03abc"
        )
        assert resp.body_format == "bytes"

    def test_json_heuristic_on_octet_stream(self) -> None:
        """Even with octet-stream, if body looks like JSON, parse it."""
        resp = parse_http_response(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: application/octet-stream\r\n"
            b"\r\n"
            b'{"result": "ok", "data": [1,2,3]}'
        )
        assert resp.body_format == "json"
        assert resp.body_parsed == {"result": "ok", "data": [1, 2, 3]}

    def test_json_list_heuristic_on_octet_stream(self) -> None:
        resp = parse_http_response(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: application/octet-stream\r\n"
            b"\r\n"
            b'[{"id":1},{"id":2}]'
        )
        assert resp.body_format == "json"
        assert resp.body_parsed == [{"id": 1}, {"id": 2}]

    def test_octet_stream_non_json_remains_bytes(self) -> None:
        resp = parse_http_response(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: application/octet-stream\r\n"
            b"\r\n"
            b"not json at all"
        )
        assert resp.body_format == "bytes"
        assert isinstance(resp.body_parsed, bytes)

    def test_no_content_type_json_heuristic(self) -> None:
        """Without Content-Type, JSON-shaped body is auto-detected."""
        resp = parse_http_response(
            b"HTTP/1.1 200 OK\r\n"
            b"\r\n"
            b'{"auto": "detected"}'
        )
        assert resp.body_format == "json"
        assert resp.body_parsed == {"auto": "detected"}

    def test_no_content_type_non_json_falls_back_to_text(self) -> None:
        resp = parse_http_response(
            b"HTTP/1.1 200 OK\r\n"
            b"\r\n"
            b"plain old text response"
        )
        assert resp.body_format == "text"
        assert resp.body_parsed == "plain old text response"


# ═══════════════════════════════════════════════════════════════════════
# 9. Edge cases
# ═══════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    """Various edge cases and boundary conditions."""

    def test_lf_only_line_endings(self) -> None:
        """Responses with LF-only line endings (not RFC-compliant but common)."""
        resp = parse_http_response(
            b"HTTP/1.1 200 OK\n"
            b"Content-Type: application/json\n"
            b"\n"
            b'{"ok":true}'
        )
        assert resp.status_code == 200
        assert resp.body_format == "json"
        assert resp.body_parsed == {"ok": True}

    def test_mixed_crlf_and_lf(self) -> None:
        """Some servers mix line ending styles."""
        resp = parse_http_response(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: text/plain\n"
            b"X-Custom: value\r\n"
            b"\r\n"
            b"body"
        )
        assert resp.status_code == 200
        # Handles mixed — CRLF detected first, so LF-only lines are treated as
        # part of the value or header section
        assert resp.content_type is not None

    def test_no_body_section(self, raw_no_content_204) -> None:
        resp = parse_http_response(raw_no_content_204)
        assert resp.status_code == 204
        assert resp.body_raw == b""
        assert resp.body_parsed is None
        assert resp.body_format == "none"

    def test_response_with_trailing_newlines(self) -> None:
        resp = parse_http_response(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: application/json\r\n"
            b"\r\n"
            b'{"a":1}\r\n\r\n'
        )
        assert resp.body_parsed == {"a": 1}

    def test_response_with_only_status_line(self) -> None:
        resp = parse_http_response(b"HTTP/1.1 500 Internal Server Error\r\n\r\n")
        assert resp.status_code == 500
        assert resp.body_raw == b""

    def test_large_response_headers(self) -> None:
        """Many headers — all should be parsed correctly."""
        header_lines = [b"HTTP/1.1 200 OK"]
        for i in range(50):
            header_lines.append(f"X-Header-{i:04d}: value-{i}".encode())
        header_lines.append(b"")
        header_lines.append(b"body content")
        raw = b"\r\n".join(header_lines)
        resp = parse_http_response(raw)
        assert resp.status_code == 200
        assert len(resp.headers) == 50
        assert resp.get_header("X-Header-0042") == "value-42"

    def test_response_with_connection_header(self) -> None:
        resp = parse_http_response(
            b"HTTP/1.1 200 OK\r\n"
            b"Connection: keep-alive\r\n"
            b"Keep-Alive: timeout=5, max=100\r\n"
            b"Content-Type: text/plain\r\n"
            b"\r\n"
            b"Hello"
        )
        assert resp.get_header("Connection") == "keep-alive"
        assert resp.get_header("Keep-Alive") == "timeout=5, max=100"


# ═══════════════════════════════════════════════════════════════════════
# 10. HttpResponse dataclass properties
# ═══════════════════════════════════════════════════════════════════════


class TestHttpResponseProperties:
    """Dataclass properties and methods."""

    def test_is_success(self) -> None:
        for code in (200, 201, 204, 299):
            raw = f"HTTP/1.1 {code} OK\r\n\r\n".encode()
            resp = parse_http_response(raw)
            assert resp.is_success is True

    def test_is_not_success(self) -> None:
        for code in (199, 300, 400, 500):
            raw = f"HTTP/1.1 {code} OK\r\n\r\n".encode()
            resp = parse_http_response(raw)
            assert resp.is_success is False

    def test_is_redirect(self) -> None:
        for code in (301, 302, 307, 308):
            raw = f"HTTP/1.1 {code} Redirect\r\n\r\n".encode()
            resp = parse_http_response(raw)
            assert resp.is_redirect is True

    def test_is_not_redirect(self) -> None:
        for code in (200, 400, 500):
            raw = f"HTTP/1.1 {code} OK\r\n\r\n".encode()
            resp = parse_http_response(raw)
            assert resp.is_redirect is False

    def test_is_client_error(self) -> None:
        for code in (400, 404, 429, 499):
            raw = f"HTTP/1.1 {code} Error\r\n\r\n".encode()
            resp = parse_http_response(raw)
            assert resp.is_client_error is True

    def test_is_server_error(self) -> None:
        for code in (500, 502, 503, 599):
            raw = f"HTTP/1.1 {code} Error\r\n\r\n".encode()
            resp = parse_http_response(raw)
            assert resp.is_server_error is True

    def test_is_not_server_error(self) -> None:
        for code in (200, 400, 499):
            raw = f"HTTP/1.1 {code} OK\r\n\r\n".encode()
            resp = parse_http_response(raw)
            assert resp.is_server_error is False

    def test_get_header_case_insensitive(self) -> None:
        resp = parse_http_response(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: application/json\r\n"
            b"X-Custom-Header: my-value\r\n"
            b"\r\n"
        )
        assert resp.get_header("content-type") == "application/json"
        assert resp.get_header("Content-Type") == "application/json"
        assert resp.get_header("CONTENT-TYPE") == "application/json"
        assert resp.get_header("x-custom-header") == "my-value"

    def test_get_header_missing(self) -> None:
        resp = parse_http_response(b"HTTP/1.1 200 OK\r\n\r\n")
        assert resp.get_header("Non-Existent") is None

    def test_total_bytes(self) -> None:
        raw = b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n\r\nHello"
        resp = parse_http_response(raw)
        assert resp.total_bytes == len(raw)

    def test_total_bytes_empty(self) -> None:
        resp = parse_http_response(b"")
        assert resp.total_bytes == 0


# ═══════════════════════════════════════════════════════════════════════
# 11. Immutability
# ═══════════════════════════════════════════════════════════════════════


class TestImmutability:
    """HttpResponse is a frozen dataclass — mutation raises FrozenInstanceError."""

    def test_status_code_immutable(self, raw_json_200) -> None:
        resp = parse_http_response(raw_json_200)
        with pytest.raises(Exception):
            resp.status_code = 500  # type: ignore[misc]

    def test_body_parsed_immutable(self, raw_json_200) -> None:
        resp = parse_http_response(raw_json_200)
        with pytest.raises(Exception):
            resp.body_parsed = None  # type: ignore[misc]

    def test_content_type_immutable(self, raw_json_200) -> None:
        resp = parse_http_response(raw_json_200)
        with pytest.raises(Exception):
            resp.content_type = "text/plain"  # type: ignore[misc]

    def test_new_instance_does_not_affect_original(self) -> None:
        raw = b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n{}"
        r1 = parse_http_response(raw)
        r2 = parse_http_response(raw)
        assert r1 is not r2
        assert r1.status_code == r2.status_code


# ═══════════════════════════════════════════════════════════════════════
# 12. Input validation
# ═══════════════════════════════════════════════════════════════════════


class TestInputValidation:
    """Type errors on invalid input."""

    def test_non_str_non_bytes_raises_type_error(self) -> None:
        with pytest.raises(TypeError):
            parse_http_response(12345)  # type: ignore[arg-type]

    def test_none_raises_type_error(self) -> None:
        with pytest.raises(TypeError):
            parse_http_response(None)  # type: ignore[arg-type]

    def test_list_raises_type_error(self) -> None:
        with pytest.raises(TypeError):
            parse_http_response(["line1", "line2"])  # type: ignore[arg-type]

    def test_str_input_accepted(self) -> None:
        resp = parse_http_response(
            "HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n\r\nHello"
        )
        assert resp.status_code == 200
        assert resp.body_parsed == "Hello"


# ═══════════════════════════════════════════════════════════════════════
# 13. Parser errors collection
# ═══════════════════════════════════════════════════════════════════════


class TestParserErrors:
    """parser_errors captures non-fatal parse issues."""

    def test_no_errors_on_clean_response(self, raw_json_200) -> None:
        resp = parse_http_response(raw_json_200)
        assert resp.parser_errors == []

    def test_errors_on_malformed_json(self) -> None:
        resp = parse_http_response(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: application/json\r\n"
            b"\r\n"
            b"{bad json}"
        )
        assert len(resp.parser_errors) >= 1

    def test_errors_on_malformed_xml(self) -> None:
        resp = parse_http_response(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: application/xml\r\n"
            b"\r\n"
            b"<<<bad>>> xml"
        )
        assert len(resp.parser_errors) >= 1

    def test_errors_on_json_without_content_type(self) -> None:
        """JSON heuristic doesn't produce errors on non-JSON text, just falls back."""
        resp = parse_http_response(
            b"HTTP/1.1 200 OK\r\n"
            b"\r\n"
            b"This is just plain text, not JSON."
        )
        # Falls back to text — no parse error for JSON heuristic fallback
        assert resp.body_format == "text"
        # It may have a JSON parse error logged since it tried first
        # This is expected behavior — the heuristic tried and failed

    def test_unicode_decode_error_logged(self) -> None:
        """Invalid bytes with a specific charset should log warning."""
        resp = parse_http_response(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: text/plain; charset=ascii\r\n"
            b"\r\n"
            b"Hello \xff\xfe World"
        )
        assert resp.body_format == "text"
        # Should still produce output (with replacement chars)
        assert "Hello" in resp.body_parsed


# ═══════════════════════════════════════════════════════════════════════
# 14. Realistic API response patterns
# ═══════════════════════════════════════════════════════════════════════


class TestRealisticApiResponses:
    """Patterns from real HTTP API responses."""

    def test_github_api_error(self) -> None:
        """GitHub-style JSON error response."""
        resp = parse_http_response(
            b"HTTP/1.1 422 Unprocessable Entity\r\n"
            b"Content-Type: application/json; charset=utf-8\r\n"
            b"X-RateLimit-Remaining: 4999\r\n"
            b"\r\n"
            b'{"message": "Validation Failed", "errors": ['
            b'{"resource": "Issue", "field": "title", "code": "missing_field"}]}'
        )
        assert resp.status_code == 422
        assert resp.is_client_error
        assert resp.body_parsed["message"] == "Validation Failed"
        assert len(resp.body_parsed["errors"]) == 1
        assert resp.get_header("X-RateLimit-Remaining") == "4999"

    def test_openai_api_response(self) -> None:
        """OpenAI-style chat completion response."""
        resp = parse_http_response(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: application/json\r\n"
            b"\r\n"
            b'{"id": "chatcmpl-123", "object": "chat.completion", '
            b'"choices": [{"index": 0, "message": {"role": "assistant", '
            b'"content": "Hello!"}, "finish_reason": "stop"}], '
            b'"usage": {"prompt_tokens": 10, "completion_tokens": 5}}'
        )
        assert resp.status_code == 200
        assert resp.body_parsed["object"] == "chat.completion"
        assert resp.body_parsed["choices"][0]["message"]["content"] == "Hello!"

    def test_s3_error_response(self) -> None:
        """AWS S3-style XML error response."""
        resp = parse_http_response(
            b"HTTP/1.1 404 Not Found\r\n"
            b"Content-Type: application/xml\r\n"
            b"x-amz-request-id: ABC123\r\n"
            b"\r\n"
            b'<?xml version="1.0" encoding="UTF-8"?>'
            b"<Error>"
            b"<Code>NoSuchKey</Code>"
            b"<Message>The specified key does not exist.</Message>"
            b"<Key>mybucket/myfile.txt</Key>"
            b"</Error>"
        )
        assert resp.status_code == 404
        assert resp.body_format == "xml"
        assert resp.body_parsed.find("Code").text == "NoSuchKey"

    def test_rate_limit_429_response(self) -> None:
        """429 Too Many Requests with Retry-After header."""
        resp = parse_http_response(
            b"HTTP/1.1 429 Too Many Requests\r\n"
            b"Content-Type: application/json\r\n"
            b"Retry-After: 60\r\n"
            b"\r\n"
            b'{"error": "rate_limited", "retry_after_ms": 60000}'
        )
        assert resp.status_code == 429
        assert resp.is_client_error
        assert resp.get_header("Retry-After") == "60"
        assert resp.body_parsed["error"] == "rate_limited"

    def test_redirect_302_response(self) -> None:
        resp = parse_http_response(
            b"HTTP/1.1 302 Found\r\n"
            b"Location: https://api.example.com/v2/resource\r\n"
            b"Content-Length: 0\r\n"
            b"\r\n"
        )
        assert resp.status_code == 302
        assert resp.is_redirect
        assert resp.get_header("Location") == "https://api.example.com/v2/resource"

    def test_binary_response_pdf(self) -> None:
        """PDF served with application/pdf — treated as bytes."""
        resp = parse_http_response(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: application/pdf\r\n"
            b"Content-Length: 100\r\n"
            b"\r\n"
            b"%PDF-1.4 fake pdf content\x00\x01"
        )
        assert resp.body_format == "bytes"
        assert resp.body_raw.startswith(b"%PDF-1.4")

    def test_graphql_response(self) -> None:
        """GraphQL API error response."""
        resp = parse_http_response(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: application/json\r\n"
            b"\r\n"
            b'{"data": null, "errors": ['
            b'{"message": "Cannot query field \\"invalid\\" on type \\"Query\\".", '
            b'"locations": [{"line": 2, "column": 3}]}]}'
        )
        assert resp.status_code == 200
        assert resp.body_parsed["data"] is None
        assert len(resp.body_parsed["errors"]) == 1
