"""Tests for the action descriptor validator module (Sub-AC 15.1a).

Covers:
- Valid action descriptors (all required fields present, valid types)
- Valid descriptors with optional timeout
- Missing required fields (each individually: method, url, headers, body)
- Invalid HTTP methods
- URL format validation: missing scheme, unsupported scheme, no host, empty
- Headers validation: non-dict, non-string keys, non-string values, empty dict OK
- Body validation: valid types (str, dict, bytes, None), invalid types
- Timeout validation: negative, zero, bool, non-numeric
- Multiple simultaneous errors
- Edge cases: empty descriptor, non-dict raises TypeError
- Result dataclass: to_dict, errors_by_field, properties
- Realistic OpenClaw action descriptor patterns
"""

from __future__ import annotations

import pytest

from src.action_descriptor_validator import (
    ActionDescriptorValidationError,
    ActionDescriptorValidationResult,
    VALID_HTTP_METHODS,
    VALID_URL_SCHEMES,
    validate_action_descriptor,
)


# ═══════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════


@pytest.fixture
def valid_descriptor() -> dict:
    """A minimal valid action descriptor (POST JSON)."""
    return {
        "method": "POST",
        "url": "https://api.example.com/v1/deploy",
        "headers": {"Authorization": "Bearer abc123", "Content-Type": "application/json"},
        "body": {"target": "prod", "version": "1.2.0"},
    }


@pytest.fixture
def valid_descriptor_get() -> dict:
    """A valid GET action descriptor (no body needed, body=None is acceptable)."""
    return {
        "method": "GET",
        "url": "https://api.example.com/v1/status",
        "headers": {"Accept": "application/json"},
        "body": None,
    }


@pytest.fixture
def valid_descriptor_with_timeout() -> dict:
    """A valid descriptor with optional timeout."""
    return {
        "method": "PUT",
        "url": "https://api.example.com/v1/config",
        "headers": {"Authorization": "Bearer xyz", "Content-Type": "application/json"},
        "body": '{"key": "value"}',
        "timeout": 30.0,
    }


# ═══════════════════════════════════════════════════════════════════════
# 1. Valid descriptors — happy path
# ═══════════════════════════════════════════════════════════════════════


class TestValidDescriptors:
    """All valid action descriptors pass validation."""

    def test_valid_post_descriptor_passes(self, valid_descriptor) -> None:
        result = validate_action_descriptor(valid_descriptor)
        assert result.passed is True
        assert result.error_count == 0
        assert result.total_fields_checked == 4

    def test_valid_get_descriptor_passes(self, valid_descriptor_get) -> None:
        result = validate_action_descriptor(valid_descriptor_get)
        assert result.passed is True
        assert result.error_count == 0

    def test_valid_descriptor_with_timeout_passes(self, valid_descriptor_with_timeout) -> None:
        result = validate_action_descriptor(valid_descriptor_with_timeout)
        assert result.passed is True
        assert result.error_count == 0
        assert result.total_fields_checked == 5

    @pytest.mark.parametrize("method", sorted(VALID_HTTP_METHODS))
    def test_all_valid_methods_pass(self, method: str, valid_descriptor) -> None:
        d = dict(valid_descriptor)
        d["method"] = method
        result = validate_action_descriptor(d)
        assert result.passed is True

    def test_body_string_passes(self, valid_descriptor) -> None:
        d = dict(valid_descriptor)
        d["body"] = '{"key": "value"}'
        result = validate_action_descriptor(d)
        assert result.passed is True

    def test_body_bytes_passes(self, valid_descriptor) -> None:
        d = dict(valid_descriptor)
        d["body"] = b'{"key": "value"}'
        result = validate_action_descriptor(d)
        assert result.passed is True

    def test_body_none_passes(self, valid_descriptor) -> None:
        d = dict(valid_descriptor)
        d["body"] = None
        result = validate_action_descriptor(d)
        assert result.passed is True

    def test_empty_headers_dict_passes(self, valid_descriptor) -> None:
        d = dict(valid_descriptor)
        d["headers"] = {}
        result = validate_action_descriptor(d)
        assert result.passed is True

    def test_url_with_query_params_passes(self, valid_descriptor) -> None:
        d = dict(valid_descriptor)
        d["url"] = "https://api.example.com/v1/search?q=test&limit=10"
        result = validate_action_descriptor(d)
        assert result.passed is True

    def test_url_with_port_passes(self, valid_descriptor) -> None:
        d = dict(valid_descriptor)
        d["url"] = "https://api.example.com:8443/v1/data"
        result = validate_action_descriptor(d)
        assert result.passed is True

    def test_localhost_url_passes(self, valid_descriptor) -> None:
        d = dict(valid_descriptor)
        d["url"] = "http://localhost:8080/api/test"
        result = validate_action_descriptor(d)
        assert result.passed is True

    def test_ip_address_url_passes(self, valid_descriptor) -> None:
        d = dict(valid_descriptor)
        d["url"] = "https://192.168.1.1/api/status"
        result = validate_action_descriptor(d)
        assert result.passed is True

    def test_method_lowercase_normalizes(self, valid_descriptor) -> None:
        d = dict(valid_descriptor)
        d["method"] = "post"
        result = validate_action_descriptor(d)
        assert result.passed is True

    def test_method_with_whitespace_normalizes(self) -> None:
        d = {
            "method": "  POST  ",
            "url": "https://example.com/api",
            "headers": {},
            "body": None,
        }
        result = validate_action_descriptor(d)
        assert result.passed is True


# ═══════════════════════════════════════════════════════════════════════
# 2. Missing required fields
# ═══════════════════════════════════════════════════════════════════════


class TestMissingFields:
    """Each required field absence is detected individually."""

    def test_missing_method(self) -> None:
        d = {
            "url": "https://example.com/api",
            "headers": {},
            "body": None,
        }
        result = validate_action_descriptor(d)
        assert result.passed is False
        assert result.error_count == 1
        err = result.errors[0]
        assert err.field_name == "method"
        assert err.error_type == "missing"

    def test_missing_url(self) -> None:
        d = {
            "method": "GET",
            "headers": {},
            "body": None,
        }
        result = validate_action_descriptor(d)
        assert result.passed is False
        assert result.error_count == 1
        assert result.errors[0].field_name == "url"

    def test_missing_headers(self) -> None:
        d = {
            "method": "GET",
            "url": "https://example.com/api",
            "body": None,
        }
        result = validate_action_descriptor(d)
        assert result.passed is False
        assert result.error_count == 1
        assert result.errors[0].field_name == "headers"

    def test_missing_body(self) -> None:
        d = {
            "method": "GET",
            "url": "https://example.com/api",
            "headers": {},
        }
        result = validate_action_descriptor(d)
        assert result.passed is False
        assert result.error_count == 1
        assert result.errors[0].field_name == "body"

    def test_multiple_missing_fields(self) -> None:
        d: dict[str, str] = {}
        result = validate_action_descriptor(d)
        assert result.passed is False
        assert result.error_count == 4
        missing_fields = {e.field_name for e in result.errors}
        assert missing_fields == {"method", "url", "headers", "body"}

    def test_two_missing_fields(self) -> None:
        d = {
            "url": "https://example.com/api",
            "body": None,
        }
        result = validate_action_descriptor(d)
        assert result.passed is False
        assert result.error_count == 2
        missing_fields = {e.field_name for e in result.errors}
        assert missing_fields == {"method", "headers"}


# ═══════════════════════════════════════════════════════════════════════
# 3. Invalid method
# ═══════════════════════════════════════════════════════════════════════


class TestInvalidMethod:
    """Unsupported HTTP methods are detected."""

    def test_invalid_method_string(self, valid_descriptor) -> None:
        d = dict(valid_descriptor)
        d["method"] = "INVALID"
        result = validate_action_descriptor(d)
        assert result.passed is False
        err = result.errors[0]
        assert err.field_name == "method"
        assert err.error_type == "unsupported_method"

    def test_method_is_none(self) -> None:
        d = {
            "method": None,
            "url": "https://example.com/api",
            "headers": {},
            "body": None,
        }
        result = validate_action_descriptor(d)
        assert result.passed is False
        err = result.errors[0]
        assert err.field_name == "method"
        assert err.error_type == "missing"

    def test_method_is_int(self) -> None:
        d = {
            "method": 123,
            "url": "https://example.com/api",
            "headers": {},
            "body": None,
        }
        result = validate_action_descriptor(d)
        assert result.passed is False
        err = result.errors[0]
        assert err.field_name == "method"
        assert err.error_type == "wrong_type"

    def test_method_is_empty_string(self) -> None:
        d = {
            "method": "",
            "url": "https://example.com/api",
            "headers": {},
            "body": None,
        }
        result = validate_action_descriptor(d)
        assert result.passed is False
        err = result.errors[0]
        assert err.field_name == "method"
        assert err.error_type == "empty_string"

    def test_method_is_whitespace_only(self) -> None:
        d = {
            "method": "   ",
            "url": "https://example.com/api",
            "headers": {},
            "body": None,
        }
        result = validate_action_descriptor(d)
        assert result.passed is False
        err = result.errors[0]
        assert err.error_type == "empty_string"

    def test_non_standard_but_real_method_rejected(self, valid_descriptor) -> None:
        """TRACE and CONNECT are real HTTP methods but not in our allowed set."""
        d = dict(valid_descriptor)
        d["method"] = "TRACE"
        result = validate_action_descriptor(d)
        assert result.passed is False
        assert result.errors[0].error_type == "unsupported_method"


# ═══════════════════════════════════════════════════════════════════════
# 4. Invalid URL
# ═══════════════════════════════════════════════════════════════════════


class TestInvalidUrl:
    """Malformed or unsupported URLs are detected."""

    def test_url_none(self) -> None:
        d = {
            "method": "GET",
            "url": None,
            "headers": {},
            "body": None,
        }
        result = validate_action_descriptor(d)
        err = result.errors[0]
        assert err.field_name == "url"
        assert err.error_type == "missing"

    def test_url_is_int(self) -> None:
        d = {
            "method": "GET",
            "url": 42,
            "headers": {},
            "body": None,
        }
        result = validate_action_descriptor(d)
        err = result.errors[0]
        assert err.field_name == "url"
        assert err.error_type == "wrong_type"

    def test_url_empty_string(self) -> None:
        d = {
            "method": "GET",
            "url": "",
            "headers": {},
            "body": None,
        }
        result = validate_action_descriptor(d)
        err = result.errors[0]
        assert err.field_name == "url"
        assert err.error_type == "empty_string"

    def test_url_no_scheme(self) -> None:
        d = {
            "method": "GET",
            "url": "api.example.com/v1/data",
            "headers": {},
            "body": None,
        }
        result = validate_action_descriptor(d)
        assert result.passed is False
        err = result.errors[0]
        assert err.field_name == "url"
        assert err.error_type == "invalid_url"

    def test_url_ftp_scheme(self) -> None:
        d = {
            "method": "GET",
            "url": "ftp://files.example.com/data",
            "headers": {},
            "body": None,
        }
        result = validate_action_descriptor(d)
        assert result.passed is False
        err = result.errors[0]
        assert err.field_name == "url"
        assert err.error_type == "unsupported_scheme"

    def test_url_ws_scheme(self) -> None:
        d = {
            "method": "GET",
            "url": "ws://example.com/socket",
            "headers": {},
            "body": None,
        }
        result = validate_action_descriptor(d)
        err = result.errors[0]
        assert err.error_type == "unsupported_scheme"

    def test_url_no_host(self) -> None:
        d = {
            "method": "GET",
            "url": "https:///path/to/resource",
            "headers": {},
            "body": None,
        }
        result = validate_action_descriptor(d)
        assert result.passed is False
        # Should have an invalid_url error for missing host
        assert any(e.error_type == "invalid_url" and "host" in e.message.lower()
                   for e in result.errors)

    def test_url_no_host_and_bad_scheme(self) -> None:
        """URL with unsupported scheme AND no host — collects both errors."""
        d = {
            "method": "GET",
            "url": "ftp:///path",
            "headers": {},
            "body": None,
        }
        result = validate_action_descriptor(d)
        # Both unsupported_scheme and invalid_url (no host) should be reported
        error_types = {e.error_type for e in result.errors}
        assert "unsupported_scheme" in error_types
        assert "invalid_url" in error_types

    def test_url_with_path_only(self) -> None:
        d = {
            "method": "GET",
            "url": "/api/v1/data",
            "headers": {},
            "body": None,
        }
        result = validate_action_descriptor(d)
        assert result.passed is False
        assert result.errors[0].field_name == "url"


# ═══════════════════════════════════════════════════════════════════════
# 5. Invalid headers
# ═══════════════════════════════════════════════════════════════════════


class TestInvalidHeaders:
    """Headers type and content violations are detected."""

    def test_headers_none(self) -> None:
        d = {
            "method": "GET",
            "url": "https://example.com/api",
            "headers": None,
            "body": None,
        }
        result = validate_action_descriptor(d)
        err = result.errors[0]
        assert err.field_name == "headers"
        assert err.error_type == "missing"

    def test_headers_is_list(self) -> None:
        d = {
            "method": "GET",
            "url": "https://example.com/api",
            "headers": ["Authorization: Bearer xyz"],
            "body": None,
        }
        result = validate_action_descriptor(d)
        err = result.errors[0]
        assert err.field_name == "headers"
        assert err.error_type == "wrong_type"

    def test_headers_is_string(self) -> None:
        d = {
            "method": "GET",
            "url": "https://example.com/api",
            "headers": "Authorization: Bearer xyz",
            "body": None,
        }
        result = validate_action_descriptor(d)
        err = result.errors[0]
        assert err.field_name == "headers"
        assert err.error_type == "wrong_type"

    def test_headers_non_string_key(self) -> None:
        d = {
            "method": "GET",
            "url": "https://example.com/api",
            "headers": {123: "value"},
            "body": None,
        }
        result = validate_action_descriptor(d)
        assert result.passed is False
        err = result.errors[0]
        assert err.field_name == "headers"
        assert "non-string key" in err.message.lower()

    def test_headers_non_string_value(self) -> None:
        d = {
            "method": "GET",
            "url": "https://example.com/api",
            "headers": {"Authorization": 12345},
            "body": None,
        }
        result = validate_action_descriptor(d)
        assert result.passed is False
        # Should have a non-string values error
        assert any("non-string value" in e.message.lower() for e in result.errors)

    def test_headers_mixed_non_string(self) -> None:
        d = {
            "method": "GET",
            "url": "https://example.com/api",
            "headers": {1: "v1", "ok": True},
            "body": None,
        }
        result = validate_action_descriptor(d)
        error_types = {e.error_type for e in result.errors}
        assert "wrong_type" in error_types
        assert result.error_count >= 1


# ═══════════════════════════════════════════════════════════════════════
# 6. Invalid body
# ═══════════════════════════════════════════════════════════════════════


class TestInvalidBody:
    """Unacceptable body types are detected."""

    def test_body_is_int(self, valid_descriptor) -> None:
        d = dict(valid_descriptor)
        d["body"] = 42
        result = validate_action_descriptor(d)
        err = result.errors[0]
        assert err.field_name == "body"
        assert err.error_type == "wrong_type"

    def test_body_is_list(self, valid_descriptor) -> None:
        d = dict(valid_descriptor)
        d["body"] = [1, 2, 3]
        result = validate_action_descriptor(d)
        err = result.errors[0]
        assert err.field_name == "body"
        assert err.error_type == "wrong_type"

    def test_body_is_bool(self, valid_descriptor) -> None:
        d = dict(valid_descriptor)
        d["body"] = True
        result = validate_action_descriptor(d)
        err = result.errors[0]
        assert err.field_name == "body"
        assert err.error_type == "wrong_type"

    def test_body_is_float(self, valid_descriptor) -> None:
        d = dict(valid_descriptor)
        d["body"] = 3.14
        result = validate_action_descriptor(d)
        err = result.errors[0]
        assert err.field_name == "body"
        assert err.error_type == "wrong_type"


# ═══════════════════════════════════════════════════════════════════════
# 7. Invalid timeout
# ═══════════════════════════════════════════════════════════════════════


class TestInvalidTimeout:
    """Optional timeout field validation."""

    def test_timeout_negative(self, valid_descriptor_with_timeout) -> None:
        d = dict(valid_descriptor_with_timeout)
        d["timeout"] = -5
        result = validate_action_descriptor(d)
        err = result.errors[0]
        assert err.field_name == "timeout"
        assert err.error_type == "out_of_range"

    def test_timeout_zero(self, valid_descriptor_with_timeout) -> None:
        d = dict(valid_descriptor_with_timeout)
        d["timeout"] = 0
        result = validate_action_descriptor(d)
        err = result.errors[0]
        assert err.field_name == "timeout"
        assert err.error_type == "out_of_range"

    def test_timeout_is_bool(self, valid_descriptor_with_timeout) -> None:
        d = dict(valid_descriptor_with_timeout)
        d["timeout"] = True
        result = validate_action_descriptor(d)
        err = result.errors[0]
        assert err.field_name == "timeout"
        assert err.error_type == "wrong_type"
        assert "bool" in err.message.lower()

    def test_timeout_is_string(self, valid_descriptor_with_timeout) -> None:
        d = dict(valid_descriptor_with_timeout)
        d["timeout"] = "30"
        result = validate_action_descriptor(d)
        err = result.errors[0]
        assert err.field_name == "timeout"
        assert err.error_type == "wrong_type"

    def test_timeout_float_zero(self, valid_descriptor_with_timeout) -> None:
        d = dict(valid_descriptor_with_timeout)
        d["timeout"] = 0.0
        result = validate_action_descriptor(d)
        err = result.errors[0]
        assert err.field_name == "timeout"
        assert err.error_type == "out_of_range"

    def test_timeout_none_is_valid(self, valid_descriptor) -> None:
        """Explicit timeout=None is acceptable (default timeout used)."""
        d = dict(valid_descriptor)
        d["timeout"] = None
        result = validate_action_descriptor(d)
        assert result.passed is True


# ═══════════════════════════════════════════════════════════════════════
# 8. Multiple simultaneous errors
# ═══════════════════════════════════════════════════════════════════════


class TestMultipleErrors:
    """All errors are collected — no early exit on first failure."""

    def test_bad_method_and_bad_url(self) -> None:
        d = {
            "method": "INVALID",
            "url": "not-a-url",
            "headers": {},
            "body": None,
        }
        result = validate_action_descriptor(d)
        assert result.error_count >= 2
        fields = {e.field_name for e in result.errors}
        assert "method" in fields
        assert "url" in fields

    def test_missing_field_and_invalid_value(self) -> None:
        d = {
            "url": "",  # empty string = bad format
            "headers": {},
            "body": None,
        }
        result = validate_action_descriptor(d)
        # Should have: missing "method" + bad "url"
        assert result.error_count >= 2
        fields = {e.field_name for e in result.errors}
        assert "method" in fields
        assert "url" in fields

    def test_three_bad_fields(self) -> None:
        d = {
            "method": "BANANA",
            "url": "ftp://bad/url",
            "headers": [1, 2, 3],
            "body": 99,
            "timeout": -1,
        }
        result = validate_action_descriptor(d)
        assert result.error_count >= 5  # method + url(scheme+host) + headers + body + timeout


# ═══════════════════════════════════════════════════════════════════════
# 9. Edge cases
# ═══════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    """Boundary and error conditions."""

    def test_non_dict_raises_type_error(self) -> None:
        with pytest.raises(TypeError, match="must be a dict"):
            validate_action_descriptor(None)  # type: ignore[arg-type]

        with pytest.raises(TypeError, match="must be a dict"):
            validate_action_descriptor(["not", "a", "dict"])  # type: ignore[arg-type]

        with pytest.raises(TypeError, match="must be a dict"):
            validate_action_descriptor("string")  # type: ignore[arg-type]

    def test_empty_dict_detects_all_missing(self) -> None:
        result = validate_action_descriptor({})
        assert result.passed is False
        assert result.error_count == 4
        assert result.total_fields_checked == 0

    def test_extra_fields_are_ignored(self, valid_descriptor) -> None:
        """Unknown fields are silently passed through — not the validator's concern."""
        d = dict(valid_descriptor)
        d["extra_field"] = "whatever"
        d["another_extra"] = 42
        result = validate_action_descriptor(d)
        assert result.passed is True

    def test_url_with_trailing_slash(self, valid_descriptor) -> None:
        d = dict(valid_descriptor)
        d["url"] = "https://api.example.com/v1/data/"
        result = validate_action_descriptor(d)
        assert result.passed is True

    def test_url_with_fragment(self, valid_descriptor) -> None:
        d = dict(valid_descriptor)
        d["url"] = "https://api.example.com/page#section"
        result = validate_action_descriptor(d)
        assert result.passed is True

    def test_url_with_auth(self, valid_descriptor) -> None:
        d = dict(valid_descriptor)
        d["url"] = "https://user:pass@api.example.com/v1/data"
        result = validate_action_descriptor(d)
        assert result.passed is True

    def test_very_long_url(self, valid_descriptor) -> None:
        d = dict(valid_descriptor)
        d["url"] = "https://api.example.com/" + "a" * 500 + "/path"
        result = validate_action_descriptor(d)
        assert result.passed is True

    def test_url_with_special_chars(self, valid_descriptor) -> None:
        d = dict(valid_descriptor)
        d["url"] = "https://api.example.com/v1/search?q=hello%20world&lang=en"
        result = validate_action_descriptor(d)
        assert result.passed is True

    def test_body_empty_string(self, valid_descriptor) -> None:
        d = dict(valid_descriptor)
        d["body"] = ""
        result = validate_action_descriptor(d)
        assert result.passed is True

    def test_body_empty_dict(self, valid_descriptor) -> None:
        d = dict(valid_descriptor)
        d["body"] = {}
        result = validate_action_descriptor(d)
        assert result.passed is True

    def test_body_empty_bytes(self, valid_descriptor) -> None:
        d = dict(valid_descriptor)
        d["body"] = b""
        result = validate_action_descriptor(d)
        assert result.passed is True


# ═══════════════════════════════════════════════════════════════════════
# 10. Realistic OpenClaw action descriptor patterns
# ═══════════════════════════════════════════════════════════════════════


class TestRealisticPatterns:
    """Test descriptors that mirror real OpenClaw execution scenarios."""

    def test_gdrive_upload_descriptor(self) -> None:
        """OpenClaw writing a meeting artifact to Google Drive."""
        d = {
            "method": "POST",
            "url": "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart",
            "headers": {
                "Authorization": "Bearer ya29.abc123",
                "Content-Type": "application/json",
            },
            "body": '{"name": "round_1_transcript.md", "mimeType": "text/markdown", "parents": ["folder_123"]}',
            "timeout": 45.0,
        }
        result = validate_action_descriptor(d)
        assert result.passed is True

    def test_discord_webhook_descriptor(self) -> None:
        """OpenClaw sending a message to a Discord webhook."""
        d = {
            "method": "POST",
            "url": "https://discord.com/api/v10/webhooks/1234567890/abcdef_token",
            "headers": {
                "Content-Type": "application/json",
            },
            "body": {"content": "Meeting summary: Budget approved for Luna MV", "embeds": []},
        }
        result = validate_action_descriptor(d)
        assert result.passed is True

    def test_deploy_executor_descriptor(self) -> None:
        """OpenClaw deploying an artifact (high-risk action)."""
        d = {
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
        }
        result = validate_action_descriptor(d)
        assert result.passed is True

    def test_email_notification_descriptor(self) -> None:
        """OpenClaw sending an email notification about a meeting decision."""
        d = {
            "method": "POST",
            "url": "https://api.sendgrid.com/v3/mail/send",
            "headers": {
                "Authorization": "Bearer SG.api_key_here",
                "Content-Type": "application/json",
            },
            "body": {
                "from": {"email": "coordinator@ai-entertainment.internal"},
                "subject": "Meeting Decision: Luna MV Budget Approved",
                "content": [{"type": "text/plain", "value": "Budget approved: ₩50M"}],
            },
        }
        result = validate_action_descriptor(d)
        assert result.passed is True


# ═══════════════════════════════════════════════════════════════════════
# 11. Result dataclass: to_dict, errors_by_field, properties
# ═══════════════════════════════════════════════════════════════════════


class TestResultDataclass:
    """ActionDescriptorValidationResult and ActionDescriptorValidationError properties."""

    def test_result_to_dict_passed(self, valid_descriptor) -> None:
        result = validate_action_descriptor(valid_descriptor)
        d = result.to_dict()
        assert d["passed"] is True
        assert d["error_count"] == 0
        assert d["errors"] == []
        assert d["total_fields_checked"] == 4
        assert d["schema_version"] == "action-descriptor-validation.v1"

    def test_result_to_dict_with_errors(self) -> None:
        d = {
            "method": "BAD",
            "url": "",
            "headers": {},
            "body": None,
        }
        result = validate_action_descriptor(d)
        dd = result.to_dict()
        assert dd["passed"] is False
        assert dd["error_count"] >= 2
        assert len(dd["errors"]) >= 2
        for err in dd["errors"]:
            assert "field_name" in err
            assert "error_type" in err
            assert "message" in err
            assert "expected" in err
            assert "actual" in err

    def test_errors_by_field_groups_correctly(self) -> None:
        d = {
            "method": "BAD",
            "url": "",
            "headers": None,
            "body": 99,
            "timeout": -1,
        }
        result = validate_action_descriptor(d)
        grouped = result.errors_by_field()
        # Each bad field should appear
        for field in ("method", "url", "headers", "body", "timeout"):
            assert field in grouped, f"Expected '{field}' in errors_by_field"

    def test_error_count_property(self) -> None:
        result = validate_action_descriptor({})
        assert result.error_count == 4
        assert result.error_count == len(result.errors)

    def test_error_fields_match_expected(self) -> None:
        """Each error carries expected and actual values as strings."""
        d = {
            "method": 999,
            "url": None,
            "headers": {},
            "body": {},
        }
        result = validate_action_descriptor(d)
        assert result.error_count == 2  # bad method + None URL
        for err in result.errors:
            assert isinstance(err.field_name, str)
            assert isinstance(err.error_type, str)
            assert isinstance(err.message, str)
            assert isinstance(err.expected, str)
            assert isinstance(err.actual, str)


# ═══════════════════════════════════════════════════════════════════════
# 12. Constants validation
# ═══════════════════════════════════════════════════════════════════════


class TestConstants:
    """The VALID_HTTP_METHODS and VALID_URL_SCHEMES constants are correct."""

    def test_valid_methods_contains_expected(self) -> None:
        expected = {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"}
        assert VALID_HTTP_METHODS == expected

    def test_valid_schemes_contains_expected(self) -> None:
        assert VALID_URL_SCHEMES == {"http", "https"}


# ═══════════════════════════════════════════════════════════════════════
# 13. API contract: validate_action_descriptor function
# ═══════════════════════════════════════════════════════════════════════


class TestApiContract:
    """validate_action_descriptor() correctness as an API."""

    def test_returns_result_not_none(self, valid_descriptor) -> None:
        result = validate_action_descriptor(valid_descriptor)
        assert isinstance(result, ActionDescriptorValidationResult)

    def test_result_is_frozen(self, valid_descriptor) -> None:
        result = validate_action_descriptor(valid_descriptor)
        with pytest.raises(Exception):
            result.passed = False  # type: ignore[misc]

    def test_error_is_frozen(self) -> None:
        err = ActionDescriptorValidationError(
            field_name="test",
            error_type="missing",
            message="test error",
            expected="something",
            actual="nothing",
        )
        with pytest.raises(Exception):
            err.field_name = "other"  # type: ignore[misc]

    def test_errors_are_tuple_not_list(self, valid_descriptor) -> None:
        """errors is a tuple — immutable."""
        result = validate_action_descriptor({})
        assert isinstance(result.errors, tuple)

    def test_passed_result_has_empty_errors(self, valid_descriptor) -> None:
        result = validate_action_descriptor(valid_descriptor)
        assert result.errors == ()

    def test_version_string(self, valid_descriptor) -> None:
        result = validate_action_descriptor(valid_descriptor)
        assert result.schema_version == "action-descriptor-validation.v1"
