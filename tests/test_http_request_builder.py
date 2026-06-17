"""Tests for the HTTP request builder module (Sub-AC 15.1b).

Covers:
- All seven HTTP methods (GET, POST, PUT, DELETE, PATCH, HEAD, OPTIONS)
- Content types: JSON, URL-encoded form, multipart/form-data, text/plain,
  application/octet-stream
- Query parameter merging (inline URL params + extra query_params)
- Body serialization strategies (json, urlencoded, form-data, raw, none)
- Edge cases: None body, empty body, explicit vs inferred content-type,
  non-dict input, timeout preservation
- Immutability of HttpRequest dataclass
- Content-Type header injection when inferred
"""

from __future__ import annotations

import json

import pytest

from src.http_request_builder import (
    HttpRequest,
    build_http_request,
)


# ═══════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════


@pytest.fixture
def base_descriptor() -> dict:
    """Minimal valid POST descriptor for happy-path reuse."""
    return {
        "method": "POST",
        "url": "https://api.example.com/v1/items",
        "headers": {"Authorization": "Bearer abc123"},
        "body": {"name": "Test Item", "price": 9.99},
    }


# ═══════════════════════════════════════════════════════════════════════
# 1. All seven HTTP methods
# ═══════════════════════════════════════════════════════════════════════


class TestHttpMethods:
    """Each supported HTTP method produces correct method field."""

    @pytest.mark.parametrize(
        "method",
        ["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
    )
    def test_method_preserved(self, method: str) -> None:
        req = build_http_request({
            "method": method,
            "url": "https://example.com/api",
            "headers": {},
            "body": {"key": "value"} if method not in ("GET", "HEAD", "OPTIONS") else None,
        })
        assert req.method == method

    def test_get_request_no_body(self) -> None:
        req = build_http_request({
            "method": "GET",
            "url": "https://api.example.com/v1/status",
            "headers": {"Accept": "application/json"},
            "body": None,
        })
        assert req.method == "GET"
        assert req.body is None
        assert req.serializer_used == "none"

    def test_head_request_no_body(self) -> None:
        req = build_http_request({
            "method": "HEAD",
            "url": "https://api.example.com/v1/health",
            "headers": {},
            "body": None,
        })
        assert req.body is None
        assert req.serializer_used == "none"

    def test_options_request_no_body(self) -> None:
        req = build_http_request({
            "method": "OPTIONS",
            "url": "https://api.example.com/v1/cors",
            "headers": {"Origin": "https://app.example.com"},
            "body": None,
        })
        assert req.body is None
        assert req.serializer_used == "none"

    def test_delete_with_body(self) -> None:
        """DELETE may carry a body (spec allows it though uncommon)."""
        req = build_http_request({
            "method": "DELETE",
            "url": "https://api.example.com/v1/items/batch",
            "headers": {"Content-Type": "application/json"},
            "body": {"ids": [1, 2, 3]},
        })
        assert req.method == "DELETE"
        assert req.serializer_used == "json"

    def test_post_with_body(self, base_descriptor) -> None:
        req = build_http_request(base_descriptor)
        assert req.method == "POST"
        assert req.serializer_used == "json"

    def test_put_with_body(self) -> None:
        req = build_http_request({
            "method": "PUT",
            "url": "https://api.example.com/v1/items/42",
            "headers": {"Content-Type": "application/json"},
            "body": {"name": "Updated"},
        })
        assert req.method == "PUT"
        assert req.serializer_used == "json"

    def test_patch_with_body(self) -> None:
        req = build_http_request({
            "method": "PATCH",
            "url": "https://api.example.com/v1/items/42",
            "headers": {"Content-Type": "application/json"},
            "body": {"price": 14.99},
        })
        assert req.method == "PATCH"
        assert req.serializer_used == "json"


# ═══════════════════════════════════════════════════════════════════════
# 2. JSON content-type body serialization
# ═══════════════════════════════════════════════════════════════════════


class TestJsonSerialization:
    """Dict bodies are serialized to JSON strings."""

    def test_dict_body_serializes_to_json(self, base_descriptor) -> None:
        req = build_http_request(base_descriptor)
        assert req.serializer_used == "json"
        assert req.content_type == "application/json"
        parsed = json.loads(str(req.body))
        assert parsed == {"name": "Test Item", "price": 9.99}

    def test_explicit_json_content_type(self) -> None:
        req = build_http_request({
            "method": "POST",
            "url": "https://example.com/api",
            "headers": {"Content-Type": "application/json"},
            "body": {"a": 1},
        })
        assert req.serializer_used == "json"
        assert "application/json" in (req.content_type or "")

    def test_json_with_charset(self) -> None:
        req = build_http_request({
            "method": "POST",
            "url": "https://example.com/api",
            "headers": {"Content-Type": "application/json; charset=utf-8"},
            "body": {"a": 1},
        })
        assert req.serializer_used == "json"

    def test_already_json_string_passes_through(self) -> None:
        req = build_http_request({
            "method": "POST",
            "url": "https://example.com/api",
            "headers": {"Content-Type": "application/json"},
            "body": '{"pre_serialized": true}',
        })
        assert req.serializer_used == "json"
        assert req.body == '{"pre_serialized": true}'

    def test_json_with_unicode_content(self) -> None:
        req = build_http_request({
            "method": "POST",
            "url": "https://example.com/api",
            "headers": {"Content-Type": "application/json"},
            "body": {"message": "안녕하세요", "emoji": "🎉"},
        })
        parsed = json.loads(str(req.body))
        assert parsed["message"] == "안녕하세요"
        assert parsed["emoji"] == "🎉"

    def test_json_empty_dict(self) -> None:
        req = build_http_request({
            "method": "POST",
            "url": "https://example.com/api",
            "headers": {"Content-Type": "application/json"},
            "body": {},
        })
        assert req.body == "{}"

    def test_json_nested_structures(self) -> None:
        body = {
            "meta": {"page": 1, "limit": 20},
            "items": [{"id": 1}, {"id": 2}],
            "active": True,
            "score": None,
        }
        req = build_http_request({
            "method": "POST",
            "url": "https://example.com/api",
            "headers": {"Content-Type": "application/json"},
            "body": body,
        })
        parsed = json.loads(str(req.body))
        assert parsed == body


# ═══════════════════════════════════════════════════════════════════════
# 3. URL-encoded form body serialization
# ═══════════════════════════════════════════════════════════════════════


class TestUrlencodedSerialization:
    """application/x-www-form-urlencoded body serialization."""

    def test_dict_to_urlencoded(self) -> None:
        req = build_http_request({
            "method": "POST",
            "url": "https://example.com/login",
            "headers": {"Content-Type": "application/x-www-form-urlencoded"},
            "body": {"username": "admin", "password": "secret"},
        })
        assert req.serializer_used == "urlencoded"
        assert "username=admin" in str(req.body)
        assert "password=secret" in str(req.body)

    def test_urlencoded_string_passes_through(self) -> None:
        req = build_http_request({
            "method": "POST",
            "url": "https://example.com/login",
            "headers": {"Content-Type": "application/x-www-form-urlencoded"},
            "body": "key1=val1&key2=val2",
        })
        assert req.serializer_used == "urlencoded"
        assert req.body == "key1=val1&key2=val2"

    def test_urlencoded_special_chars(self) -> None:
        req = build_http_request({
            "method": "POST",
            "url": "https://example.com/search",
            "headers": {"Content-Type": "application/x-www-form-urlencoded"},
            "body": {"q": "hello world", "lang": "ko"},
        })
        assert "q=hello+world" in str(req.body) or "q=hello%20world" in str(req.body)

    def test_urlencoded_multiple_values(self) -> None:
        req = build_http_request({
            "method": "POST",
            "url": "https://example.com/api",
            "headers": {"Content-Type": "application/x-www-form-urlencoded"},
            "body": {"tags": ["python", "ai", "agent"]},
        })
        assert "tags=python" in str(req.body)
        assert "tags=ai" in str(req.body)
        assert "tags=agent" in str(req.body)


# ═══════════════════════════════════════════════════════════════════════
# 4. Multipart/form-data serialization
# ═══════════════════════════════════════════════════════════════════════


class TestFormDataSerialization:
    """multipart/form-data with fields and file parts."""

    def test_form_data_with_fields(self) -> None:
        req = build_http_request({
            "method": "POST",
            "url": "https://example.com/upload",
            "headers": {"Content-Type": "multipart/form-data"},
            "body": {"title": "Report", "author": "Kim"},
        })
        assert req.serializer_used == "form-data"
        assert "multipart/form-data; boundary=" in (req.content_type or "")
        assert "Content-Disposition: form-data; name=\"title\"" in str(req.body)
        assert "Report" in str(req.body)

    def test_form_data_with_file(self) -> None:
        req = build_http_request({
            "method": "POST",
            "url": "https://example.com/upload",
            "headers": {"Content-Type": "multipart/form-data"},
            "body": {
                "file": {
                    "filename": "report.md",
                    "content": "# Meeting Notes\n\nDecision: Approved.",
                    "content_type": "text/markdown",
                },
            },
        })
        assert req.serializer_used == "form-data"
        body_str = str(req.body)
        assert 'filename="report.md"' in body_str
        assert "Content-Type: text/markdown" in body_str
        assert "# Meeting Notes" in body_str

    def test_form_data_mixed_fields_and_files(self) -> None:
        req = build_http_request({
            "method": "POST",
            "url": "https://example.com/upload",
            "headers": {"Content-Type": "multipart/form-data"},
            "body": {
                "title": "Q1 Report",
                "attachment": {
                    "filename": "data.csv",
                    "content": "name,value\ntest,123",
                    "content_type": "text/csv",
                },
                "notify": "true",
            },
        })
        body_str = str(req.body)
        assert 'name="title"' in body_str
        assert 'name="attachment"' in body_str
        assert 'name="notify"' in body_str

    def test_form_data_custom_boundary(self) -> None:
        req = build_http_request(
            {
                "method": "POST",
                "url": "https://example.com/upload",
                "headers": {"Content-Type": "multipart/form-data"},
                "body": {"field": "value"},
            },
            boundary="MyCustomBoundary123",
        )
        assert "boundary=MyCustomBoundary123" in (req.content_type or "")

    def test_form_data_string_body_passes_through(self) -> None:
        req = build_http_request({
            "method": "POST",
            "url": "https://example.com/upload",
            "headers": {"Content-Type": "multipart/form-data"},
            "body": "--boundary\r\nContent-Disposition: form-data...\r\n",
        })
        assert req.serializer_used == "form-data"
        assert isinstance(req.body, str)


# ═══════════════════════════════════════════════════════════════════════
# 5. Text content-type serialization
# ═══════════════════════════════════════════════════════════════════════


class TestTextSerialization:
    """text/* content-types are passed through as strings."""

    def test_text_plain(self) -> None:
        req = build_http_request({
            "method": "POST",
            "url": "https://example.com/log",
            "headers": {"Content-Type": "text/plain"},
            "body": "This is a plain text message.",
        })
        assert req.serializer_used == "raw"
        assert req.body == "This is a plain text message."
        assert (req.content_type or "").startswith("text/plain")

    def test_text_html(self) -> None:
        req = build_http_request({
            "method": "POST",
            "url": "https://example.com/render",
            "headers": {"Content-Type": "text/html"},
            "body": "<h1>Hello</h1>",
        })
        assert req.serializer_used == "raw"
        assert req.body == "<h1>Hello</h1>"

    def test_text_csv(self) -> None:
        req = build_http_request({
            "method": "POST",
            "url": "https://example.com/data",
            "headers": {"Content-Type": "text/csv"},
            "body": "name,value\ntest,123",
        })
        assert req.serializer_used == "raw"
        assert "name,value" in str(req.body)

    def test_text_body_bytes_decoded(self) -> None:
        req = build_http_request({
            "method": "POST",
            "url": "https://example.com/api",
            "headers": {"Content-Type": "text/plain"},
            "body": b"Hello in bytes",
        })
        assert req.serializer_used == "raw"
        assert req.body == "Hello in bytes"


# ═══════════════════════════════════════════════════════════════════════
# 6. Octet-stream / raw serialization
# ═══════════════════════════════════════════════════════════════════════


class TestRawSerialization:
    """application/octet-stream and unknown content-types pass through."""

    def test_octet_stream_bytes(self) -> None:
        req = build_http_request({
            "method": "POST",
            "url": "https://example.com/upload",
            "headers": {"Content-Type": "application/octet-stream"},
            "body": b"\x00\x01\x02\x03",
        })
        assert req.serializer_used == "raw"
        assert req.body == b"\x00\x01\x02\x03"

    def test_unknown_content_type_passes_through(self) -> None:
        req = build_http_request({
            "method": "POST",
            "url": "https://example.com/api",
            "headers": {"Content-Type": "application/vnd.custom+cbor"},
            "body": b"\x00\x01\x02",
        })
        assert req.serializer_used == "raw"
        assert req.body == b"\x00\x01\x02"

    def test_vendor_json_content_type(self) -> None:
        """application/vnd.custom+json is detected as JSON via +json suffix."""
        req = build_http_request({
            "method": "POST",
            "url": "https://example.com/api",
            "headers": {"Content-Type": "application/vnd.custom+json"},
            "body": {"custom": "format"},
        })
        assert req.serializer_used == "json"
        parsed = json.loads(str(req.body))
        assert parsed["custom"] == "format"


# ═══════════════════════════════════════════════════════════════════════
# 7. Query parameter handling
# ═══════════════════════════════════════════════════════════════════════


class TestQueryParameters:
    """Query params in URL and extra query_params are merged correctly."""

    def test_url_already_has_query_params(self) -> None:
        req = build_http_request({
            "method": "GET",
            "url": "https://api.example.com/v1/search?q=test&limit=10",
            "headers": {},
            "body": None,
        })
        assert "q=test" in req.url
        assert "limit=10" in req.url

    def test_extra_query_params_merged(self) -> None:
        req = build_http_request(
            {
                "method": "GET",
                "url": "https://api.example.com/v1/search",
                "headers": {},
                "body": None,
            },
            query_params={"q": "test", "limit": "10"},
        )
        assert "q=test" in req.url
        assert "limit=10" in req.url

    def test_descriptor_query_params_key(self) -> None:
        req = build_http_request({
            "method": "GET",
            "url": "https://api.example.com/v1/search",
            "headers": {},
            "body": None,
            "query_params": {"q": "test", "page": "2"},
        })
        assert "q=test" in req.url
        assert "page=2" in req.url

    def test_extra_overrides_inline_params(self) -> None:
        """Extra query_params override inline URL params for same keys."""
        req = build_http_request(
            {
                "method": "GET",
                "url": "https://api.example.com/v1/search?q=old&limit=5",
                "headers": {},
                "body": None,
            },
            query_params={"q": "new"},
        )
        assert "q=new" in req.url
        assert "limit=5" in req.url

    def test_descriptor_qp_overrides_arg_qp(self) -> None:
        """descriptor['query_params'] wins over function argument."""
        req = build_http_request(
            {
                "method": "GET",
                "url": "https://api.example.com/v1/search",
                "headers": {},
                "body": None,
                "query_params": {"q": "from_descriptor"},
            },
            query_params={"q": "from_arg"},
        )
        assert "q=from_descriptor" in req.url

    def test_no_query_params_clean_url(self) -> None:
        req = build_http_request({
            "method": "GET",
            "url": "https://api.example.com/v1/status",
            "headers": {},
            "body": None,
        })
        assert "?" not in req.url

    def test_fragment_stripped_from_url(self) -> None:
        req = build_http_request({
            "method": "GET",
            "url": "https://api.example.com/page#section",
            "headers": {},
            "body": None,
        })
        assert "#" not in req.url


# ═══════════════════════════════════════════════════════════════════════
# 8. URL construction correctness
# ═══════════════════════════════════════════════════════════════════════


class TestUrlConstruction:
    """URL is correctly preserved / constructed."""

    def test_https_url_preserved(self) -> None:
        req = build_http_request({
            "method": "GET",
            "url": "https://api.example.com/v1/data",
            "headers": {},
            "body": None,
        })
        assert req.url == "https://api.example.com/v1/data"

    def test_http_url_preserved(self) -> None:
        req = build_http_request({
            "method": "GET",
            "url": "http://localhost:8080/api/test",
            "headers": {},
            "body": None,
        })
        assert req.url == "http://localhost:8080/api/test"

    def test_url_with_port(self) -> None:
        req = build_http_request({
            "method": "GET",
            "url": "https://api.example.com:8443/v1/data",
            "headers": {},
            "body": None,
        })
        assert ":8443" in req.url

    def test_url_with_ip_address(self) -> None:
        req = build_http_request({
            "method": "GET",
            "url": "https://192.168.1.1/api/status",
            "headers": {},
            "body": None,
        })
        assert "192.168.1.1" in req.url

    def test_url_with_path_only_elements(self) -> None:
        req = build_http_request({
            "method": "GET",
            "url": "https://api.example.com/a/b/c/d",
            "headers": {},
            "body": None,
        })
        assert req.url == "https://api.example.com/a/b/c/d"


# ═══════════════════════════════════════════════════════════════════════
# 9. Header handling
# ═══════════════════════════════════════════════════════════════════════


class TestHeaderHandling:
    """Headers are preserved and case-insensitively searchable."""

    def test_headers_preserved(self, base_descriptor) -> None:
        req = build_http_request(base_descriptor)
        assert req.headers["Authorization"] == "Bearer abc123"

    def test_case_insensitive_get_header(self) -> None:
        req = build_http_request({
            "method": "GET",
            "url": "https://example.com/api",
            "headers": {"Authorization": "Bearer xyz", "X-Custom": "value"},
            "body": None,
        })
        assert req.get_header("authorization") == "Bearer xyz"
        assert req.get_header("AUTHORIZATION") == "Bearer xyz"
        assert req.get_header("x-custom") == "value"
        assert req.get_header("missing") is None

    def test_content_type_auto_inferred(self, base_descriptor) -> None:
        """When no Content-Type is given, it's inferred from body type."""
        req = build_http_request(base_descriptor)
        assert "Content-Type" in req.headers
        assert req.headers["Content-Type"] == "application/json"

    def test_content_type_not_overwritten_when_explicit(self) -> None:
        req = build_http_request({
            "method": "POST",
            "url": "https://example.com/api",
            "headers": {"Content-Type": "application/json; charset=utf-8"},
            "body": {"a": 1},
        })
        # Explicit Content-Type preserved; resolved_ctype may differ
        assert "charset=utf-8" in req.headers.get("Content-Type", "")

    def test_empty_headers_dict(self) -> None:
        req = build_http_request({
            "method": "GET",
            "url": "https://example.com/api",
            "headers": {},
            "body": None,
        })
        assert isinstance(req.headers, dict)
        assert len(req.headers) == 0

    def test_multiple_headers(self) -> None:
        req = build_http_request({
            "method": "POST",
            "url": "https://example.com/api",
            "headers": {
                "Authorization": "Bearer tkn",
                "X-Request-Id": "req-001",
                "Accept": "application/json",
            },
            "body": {"a": 1},
        })
        assert req.headers["Authorization"] == "Bearer tkn"
        assert req.headers["X-Request-Id"] == "req-001"


# ═══════════════════════════════════════════════════════════════════════
# 10. Timeout handling
# ═══════════════════════════════════════════════════════════════════════


class TestTimeout:
    """Optional timeout is preserved."""

    def test_timeout_preserved(self) -> None:
        req = build_http_request({
            "method": "POST",
            "url": "https://example.com/api",
            "headers": {},
            "body": {"a": 1},
            "timeout": 30.0,
        })
        assert req.timeout == 30.0

    def test_timeout_default_none(self, base_descriptor) -> None:
        req = build_http_request(base_descriptor)
        assert req.timeout is None

    def test_timeout_int(self) -> None:
        req = build_http_request({
            "method": "POST",
            "url": "https://example.com/api",
            "headers": {},
            "body": None,
            "timeout": 45,
        })
        assert req.timeout == 45

    def test_timeout_zero(self) -> None:
        """Timeout of 0 is preserved (validated upstream, not here)."""
        req = build_http_request({
            "method": "GET",
            "url": "https://example.com/api",
            "headers": {},
            "body": None,
            "timeout": 0,
        })
        assert req.timeout == 0


# ═══════════════════════════════════════════════════════════════════════
# 11. Body edge cases
# ═══════════════════════════════════════════════════════════════════════


class TestBodyEdgeCases:
    """Edge cases around body handling."""

    def test_body_none(self) -> None:
        req = build_http_request({
            "method": "POST",
            "url": "https://example.com/api",
            "headers": {},
            "body": None,
        })
        assert req.body is None
        assert req.serializer_used == "none"

    def test_body_empty_string(self) -> None:
        req = build_http_request({
            "method": "POST",
            "url": "https://example.com/api",
            "headers": {"Content-Type": "text/plain"},
            "body": "",
        })
        assert req.body == ""

    def test_body_empty_dict(self) -> None:
        req = build_http_request({
            "method": "POST",
            "url": "https://example.com/api",
            "headers": {"Content-Type": "application/json"},
            "body": {},
        })
        assert req.body == "{}"

    def test_body_empty_bytes(self) -> None:
        req = build_http_request({
            "method": "POST",
            "url": "https://example.com/api",
            "headers": {"Content-Type": "application/octet-stream"},
            "body": b"",
        })
        assert req.body == b""

    def test_body_inference_dict_no_ct(self) -> None:
        """Dict body without Content-Type → inferred as JSON."""
        req = build_http_request({
            "method": "POST",
            "url": "https://example.com/api",
            "headers": {},
            "body": {"key": "value"},
        })
        assert req.serializer_used == "json"
        assert "application/json" in (req.content_type or "")

    def test_body_inference_str_no_ct(self) -> None:
        """String body without Content-Type → inferred as text/plain."""
        req = build_http_request({
            "method": "POST",
            "url": "https://example.com/api",
            "headers": {},
            "body": "just a string",
        })
        assert req.serializer_used == "raw"
        assert (req.content_type or "").startswith("text/plain")

    def test_body_inference_bytes_no_ct(self) -> None:
        """Bytes body without Content-Type → inferred as octet-stream."""
        req = build_http_request({
            "method": "POST",
            "url": "https://example.com/api",
            "headers": {},
            "body": b"\x00\x01\x02",
        })
        assert req.serializer_used == "raw"
        assert "octet-stream" in (req.content_type or "")

    def test_method_without_body_skips_body(self) -> None:
        """GET with a body dict still gets body=None."""
        req = build_http_request({
            "method": "GET",
            "url": "https://example.com/api",
            "headers": {},
            "body": {"should": "be_ignored"},
        })
        assert req.body is None


# ═══════════════════════════════════════════════════════════════════════
# 12. Non-dict descriptor input
# ═══════════════════════════════════════════════════════════════════════


class TestInvalidInput:
    """TypeError raised for non-dict descriptors."""

    def test_none_raises_type_error(self) -> None:
        with pytest.raises(TypeError, match="must be a dict"):
            build_http_request(None)  # type: ignore[arg-type]

    def test_list_raises_type_error(self) -> None:
        with pytest.raises(TypeError, match="must be a dict"):
            build_http_request([1, 2, 3])  # type: ignore[arg-type]

    def test_string_raises_type_error(self) -> None:
        with pytest.raises(TypeError, match="must be a dict"):
            build_http_request("not a dict")  # type: ignore[arg-type]


# ═══════════════════════════════════════════════════════════════════════
# 13. Realistic OpenClaw action descriptor patterns
# ═══════════════════════════════════════════════════════════════════════


class TestRealisticPatterns:
    """Full request construction for real OpenClaw scenarios."""

    def test_gdrive_upload(self) -> None:
        req = build_http_request({
            "method": "POST",
            "url": "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart",
            "headers": {
                "Authorization": "Bearer ya29.abc123",
                "Content-Type": "application/json",
            },
            "body": {
                "name": "round_1_transcript.md",
                "mimeType": "text/markdown",
                "parents": ["folder_123"],
            },
            "timeout": 45.0,
        })
        assert req.method == "POST"
        assert "uploadType=multipart" in req.url
        assert req.get_header("authorization") == "Bearer ya29.abc123"
        assert req.timeout == 45.0
        parsed = json.loads(str(req.body))
        assert parsed["name"] == "round_1_transcript.md"

    def test_discord_webhook(self) -> None:
        req = build_http_request({
            "method": "POST",
            "url": "https://discord.com/api/v10/webhooks/1234567890/token",
            "headers": {"Content-Type": "application/json"},
            "body": {
                "content": "Meeting summary: Budget approved for Luna MV",
                "embeds": [],
            },
        })
        assert req.method == "POST"
        assert "discord.com" in req.url
        parsed = json.loads(str(req.body))
        assert "Meeting summary" in parsed["content"]

    def test_deploy_executor(self) -> None:
        req = build_http_request({
            "method": "PUT",
            "url": "https://deploy.internal.example.com/api/v2/release",
            "headers": {
                "Authorization": "Bearer deploy-token-xyz",
                "Content-Type": "application/json",
            },
            "body": {
                "artifact_id": "meeting_20260610_abc123",
                "target": "staging",
                "risk_level": "medium",
            },
            "timeout": 120.0,
        })
        assert req.method == "PUT"
        parsed = json.loads(str(req.body))
        assert parsed["risk_level"] == "medium"

    def test_email_notification(self) -> None:
        req = build_http_request({
            "method": "POST",
            "url": "https://api.sendgrid.com/v3/mail/send",
            "headers": {
                "Authorization": "Bearer sg.secret",
                "Content-Type": "application/json",
            },
            "body": {
                "from": {"email": "coordinator@ai-entertainment.internal"},
                "subject": "Meeting Decision: Luna MV Budget Approved",
                "content": [{"type": "text/plain", "value": "Budget approved"}],
            },
        })
        assert req.method == "POST"
        parsed = json.loads(str(req.body))
        assert parsed["from"]["email"] == "coordinator@ai-entertainment.internal"

    def test_health_check_get(self) -> None:
        req = build_http_request({
            "method": "GET",
            "url": "https://monitoring.internal.example.com/api/health",
            "headers": {"X-Api-Key": "mon-key-001"},
            "body": None,
        })
        assert req.method == "GET"
        assert req.body is None
        assert req.get_header("x-api-key") == "mon-key-001"

    def test_form_upload_scenario(self) -> None:
        """OpenClaw uploading a meeting artifact as multipart form."""
        req = build_http_request({
            "method": "POST",
            "url": "https://artifacts.internal.example.com/upload",
            "headers": {"Authorization": "Bearer internal-token"},
            "body": {
                "meeting_id": "mtg-001",
                "file": {
                    "filename": "transcript.md",
                    "content": "# Round 1 Transcript\n\nDiscussion...",
                    "content_type": "text/markdown",
                },
            },
        })
        # No explicit Content-Type → dict → inferred JSON
        # But body has a "file" sub-dict with "filename" key
        # Since no Content-Type, it'll be serialized as JSON
        assert req.serializer_used == "json"
        # Verify the nested structure is preserved in JSON
        parsed = json.loads(str(req.body))
        assert parsed["meeting_id"] == "mtg-001"
        assert parsed["file"]["filename"] == "transcript.md"


# ═══════════════════════════════════════════════════════════════════════
# 14. HttpRequest dataclass immutability
# ═══════════════════════════════════════════════════════════════════════


class TestHttpRequestDataclass:
    """HttpRequest is frozen and its properties work correctly."""

    def test_frozen_dataclass(self, base_descriptor) -> None:
        req = build_http_request(base_descriptor)
        with pytest.raises(Exception):
            req.method = "PUT"  # type: ignore[misc]

    def test_get_header_case_insensitive(self) -> None:
        req = build_http_request({
            "method": "GET",
            "url": "https://example.com/api",
            "headers": {"X-Custom-Header": "custom-value"},
            "body": None,
        })
        assert req.get_header("x-custom-header") == "custom-value"
        assert req.get_header("X-CUSTOM-HEADER") == "custom-value"
        assert req.get_header("X-Custom-Header") == "custom-value"

    def test_get_header_missing(self) -> None:
        req = build_http_request({
            "method": "GET",
            "url": "https://example.com/api",
            "headers": {},
            "body": None,
        })
        assert req.get_header("anything") is None

    def test_all_fields_are_present(self, base_descriptor) -> None:
        req = build_http_request(base_descriptor)
        assert isinstance(req.method, str)
        assert isinstance(req.url, str)
        assert isinstance(req.headers, dict)
        # body can be str, bytes, or None
        assert req.body is None or isinstance(req.body, (str, bytes))
        assert isinstance(req.serializer_used, str)


# ═══════════════════════════════════════════════════════════════════════
# 15. Chaining: validator + builder
# ═══════════════════════════════════════════════════════════════════════


class TestValidatorBuilderChain:
    """End-to-end: validated descriptor → HTTP request."""

    def test_chain_post_json(self) -> None:
        from src.action_descriptor_validator import validate_action_descriptor

        descriptor = {
            "method": "POST",
            "url": "https://api.example.com/v1/deploy",
            "headers": {"Authorization": "Bearer abc"},
            "body": {"target": "prod"},
        }
        validation = validate_action_descriptor(descriptor)
        assert validation.passed is True

        req = build_http_request(descriptor)
        assert req.method == "POST"
        assert req.serializer_used == "json"

    def test_chain_get_no_body(self) -> None:
        from src.action_descriptor_validator import validate_action_descriptor

        descriptor = {
            "method": "GET",
            "url": "https://api.example.com/v1/status",
            "headers": {"Accept": "application/json"},
            "body": None,
        }
        validation = validate_action_descriptor(descriptor)
        assert validation.passed is True

        req = build_http_request(descriptor)
        assert req.method == "GET"
        assert req.body is None
