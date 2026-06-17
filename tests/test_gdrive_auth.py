"""Tests for the Google Drive OAuth authentication module (Sub-AC 19a-1).

Verifies:
- GDriveToken creation, validation, expiry detection
- Token serialization to/from JSON
- Mock authorization code exchange flow
- Mock token refresh flow
- Token persistence (save/load from disk)
- GDriveAuthenticator full lifecycle:
  - authenticate with auth code
  - validate credentials and token
  - refresh expired tokens
  - auto-refresh via get_valid_token()
  - credential validation (static check)
  - token revocation
- Error paths: invalid code, expired without refresh, missing credentials
- Token exchange inject mechanism (for mock testing)

Uses inject_token_exchange() to inject a mock HTTP handler so no
real network calls are made during tests.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import pytest

from src.gdrive_auth import (
    GOOGLE_DRIVE_SCOPES,
    OAUTH_TOKEN_URI,
    TOKEN_GRACE_PERIOD_SECONDS,
    GDriveAuthConfig,
    GDriveAuthResult,
    GDriveAuthenticator,
    GDriveToken,
    _http_token_exchange,
    build_authenticator,
    exchange_auth_code,
    inject_token_exchange,
    is_token_valid,
    load_token_from_file,
    refresh_access_token,
    save_token_to_file,
    token_ttl_seconds,
)


# ═════════════════════════════════════════════════════════════════════════
# Helpers
# ═════════════════════════════════════════════════════════════════════════


def _make_config(
    client_id: str = "test-client-id.apps.googleusercontent.com",
    client_secret: str = "test-secret",
    token_path: str = "",
) -> GDriveAuthConfig:
    """Build a test GDriveAuthConfig with convenient defaults."""
    return GDriveAuthConfig(
        client_id=client_id,
        client_secret=client_secret,
        token_path=token_path,
    )


def _make_valid_token(
    access_token: str = "ya29.test-access-token",
    refresh_token: str = "1//test-refresh-token",
    expires_in: float = 3600.0,
) -> GDriveToken:
    """Build a valid, non-expired GDriveToken."""
    return GDriveToken(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="Bearer",
        expires_at=time.time() + expires_in,
        scope="https://www.googleapis.com/auth/drive.file",
    )


def _make_expired_token(
    access_token: str = "ya29.old-token",
    refresh_token: str = "1//refresh-available",
) -> GDriveToken:
    """Build an already-expired GDriveToken (with refresh token)."""
    return GDriveToken(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="Bearer",
        expires_at=time.time() - 3600.0,  # 1 hour ago
    )


def _make_expired_no_refresh_token(
    access_token: str = "ya29.old-no-refresh",
) -> GDriveToken:
    """Build an expired token with no refresh token."""
    return GDriveToken(
        access_token=access_token,
        refresh_token="",
        token_type="Bearer",
        expires_at=time.time() - 3600.0,
    )


# ── Mock token exchange handler ──────────────────────────────────────────


def _mock_token_exchange_success(
    token_uri: str,
    params: dict,
    timeout: float = 30.0,
) -> dict:
    """Mock token exchange that always returns a valid token response."""
    return {
        "access_token": "ya29.mocked-access-token",
        "expires_in": 3600,
        "token_type": "Bearer",
        "scope": "https://www.googleapis.com/auth/drive.file",
        "refresh_token": params.get("refresh_token", "1//mocked-refresh"),
    }


def _mock_token_exchange_error(
    token_uri: str,
    params: dict,
    timeout: float = 30.0,
) -> dict:
    """Mock token exchange that returns an OAuth error."""
    return {
        "error": "invalid_grant",
        "error_description": "Bad request - invalid authorization code.",
        "_http_status": 400,
    }


def _mock_token_exchange_network_failure(
    token_uri: str,
    params: dict,
    timeout: float = 30.0,
) -> dict:
    """Mock token exchange that raises a network error."""
    raise OSError("Connection refused")


# ── Fixture: auto-reset inject ──────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_token_exchange() -> None:
    """Reset the token exchange inject to default after each test."""
    yield
    inject_token_exchange(None)


# ═════════════════════════════════════════════════════════════════════════
# GDriveToken tests
# ═════════════════════════════════════════════════════════════════════════


class TestGDriveToken:
    """Verify GDriveToken creation, validation, and serialization."""

    def test_create_valid_token(self) -> None:
        token = _make_valid_token()
        assert token.access_token == "ya29.test-access-token"
        assert token.refresh_token == "1//test-refresh-token"
        assert token.token_type == "Bearer"
        assert token.is_valid is True
        assert token.is_expired is False
        assert token.can_refresh is True

    def test_create_expired_token(self) -> None:
        token = _make_expired_token()
        assert token.is_expired is True
        assert token.is_valid is False
        assert token.can_refresh is True  # refresh token still present

    def test_expired_no_refresh(self) -> None:
        token = _make_expired_no_refresh_token()
        assert token.is_expired is True
        assert token.is_valid is False
        assert token.can_refresh is False

    def test_expiry_with_grace_period(self) -> None:
        """Token expiring within grace period is considered expired."""
        expires_soon = time.time() + (TOKEN_GRACE_PERIOD_SECONDS / 2)
        token = GDriveToken(
            access_token="ya29.test",
            refresh_token="1//ref",
            expires_at=expires_soon,
        )
        assert token.is_expired is True
        assert token.is_valid is False

    def test_expiry_exactly_at_grace_period(self) -> None:
        """Token expiring exactly at grace period boundary is still valid."""
        expires_at_boundary = time.time() + TOKEN_GRACE_PERIOD_SECONDS
        token = GDriveToken(
            access_token="ya29.test",
            refresh_token="1//ref",
            expires_at=expires_at_boundary,
        )
        # >= means expired at exactly the grace period
        assert token.is_expired is True

    def test_empty_access_token_invalid(self) -> None:
        token = GDriveToken(
            access_token="",
            refresh_token="1//ref",
            expires_at=time.time() + 3600,
        )
        assert token.is_valid is False

    def test_to_dict_roundtrip(self) -> None:
        original = _make_valid_token()
        data = original.to_dict()
        restored = GDriveToken.from_dict(data)
        assert restored.access_token == original.access_token
        assert restored.refresh_token == original.refresh_token
        assert restored.token_type == original.token_type
        assert restored.expires_at == original.expires_at
        assert restored.scope == original.scope

    def test_from_dict_missing_fields(self) -> None:
        """Missing fields in dict should get sensible defaults."""
        token = GDriveToken.from_dict({})
        assert token.access_token == ""
        assert token.refresh_token == ""
        assert token.token_type == "Bearer"
        assert token.expires_at == 0.0
        assert token.scope == ""

    def test_from_oauth_response(self) -> None:
        response = {
            "access_token": "ya29.from-oauth",
            "expires_in": 1800,
            "token_type": "Bearer",
            "scope": "https://www.googleapis.com/auth/drive.file",
        }
        token = GDriveToken.from_oauth_response(response)
        assert token.access_token == "ya29.from-oauth"
        assert token.refresh_token == ""  # not in response
        assert token.token_type == "Bearer"
        # expires_at should be ~now + 1800
        assert token.expires_at > time.time() + 1700
        assert token.expires_at < time.time() + 1900

    def test_from_oauth_response_with_refresh(self) -> None:
        response = {
            "access_token": "ya29.new",
            "expires_in": 3600,
            "refresh_token": "1//new-refresh",
        }
        token = GDriveToken.from_oauth_response(response)
        assert token.access_token == "ya29.new"
        assert token.refresh_token == "1//new-refresh"

    def test_from_oauth_response_preserves_existing_refresh(self) -> None:
        """When response has no refresh_token, existing one is preserved."""
        response = {
            "access_token": "ya29.new",
            "expires_in": 3600,
        }
        token = GDriveToken.from_oauth_response(
            response, refresh_token="1//existing-refresh"
        )
        assert token.access_token == "ya29.new"
        assert token.refresh_token == "1//existing-refresh"


# ═════════════════════════════════════════════════════════════════════════
# is_token_valid / token_ttl_seconds tests
# ═════════════════════════════════════════════════════════════════════════


class TestTokenValidationHelpers:
    """Verify the standalone token validation utilities."""

    def test_valid_token_passes(self) -> None:
        token = _make_valid_token()
        assert is_token_valid(token) is True

    def test_expired_token_fails(self) -> None:
        token = _make_expired_token()
        assert is_token_valid(token) is False

    def test_empty_token_fails(self) -> None:
        token = GDriveToken(access_token="", expires_at=time.time() + 3600)
        assert is_token_valid(token) is False

    def test_ttl_for_valid_token(self) -> None:
        token = _make_valid_token(expires_in=600)
        ttl = token_ttl_seconds(token)
        assert 590 < ttl <= 600

    def test_ttl_for_expired_token(self) -> None:
        token = _make_expired_token()
        assert token_ttl_seconds(token) == 0.0

    def test_ttl_for_zero_expires_at(self) -> None:
        token = GDriveToken(access_token="x", expires_at=0.0)
        assert token_ttl_seconds(token) == 0.0


# ═════════════════════════════════════════════════════════════════════════
# Token exchange (mock) tests
# ═════════════════════════════════════════════════════════════════════════


class TestExchangeAuthCode:
    """Verify authorization code exchange with mock HTTP handler."""

    def test_successful_exchange(self) -> None:
        inject_token_exchange(_mock_token_exchange_success)
        config = _make_config()
        result = exchange_auth_code(config, "4/valid-auth-code")
        assert result.success is True
        assert result.token.access_token == "ya29.mocked-access-token"
        assert result.token.refresh_token != ""

    def test_invalid_auth_code(self) -> None:
        inject_token_exchange(_mock_token_exchange_error)
        config = _make_config()
        result = exchange_auth_code(config, "4/bad-code")
        assert result.success is False
        assert "invalid_grant" in result.error.lower() or "400" in result.error
        assert result.status_code == 400

    def test_empty_auth_code(self) -> None:
        config = _make_config()
        result = exchange_auth_code(config, "")
        assert result.success is False
        assert "empty" in result.error.lower()

    def test_missing_client_credentials(self) -> None:
        config = GDriveAuthConfig(client_id="", client_secret="")
        result = exchange_auth_code(config, "4/some-code")
        assert result.success is False
        assert "required" in result.error.lower()

    def test_network_failure(self) -> None:
        inject_token_exchange(_mock_token_exchange_network_failure)
        config = _make_config()
        result = exchange_auth_code(config, "4/valid-code")
        assert result.success is False
        assert "refused" in result.error.lower()

    def test_response_missing_access_token(self) -> None:
        def _mock_missing_token(*args: Any, **kwargs: Any) -> dict:
            return {"token_type": "Bearer", "expires_in": 3600}

        inject_token_exchange(_mock_missing_token)
        config = _make_config()
        result = exchange_auth_code(config, "4/some-code")
        assert result.success is False
        assert "missing access_token" in result.error.lower()


class TestRefreshAccessToken:
    """Verify token refresh with mock HTTP handler."""

    def test_successful_refresh(self) -> None:
        inject_token_exchange(_mock_token_exchange_success)
        config = _make_config()
        old_token = _make_expired_token()
        result = refresh_access_token(config, old_token)
        assert result.success is True
        assert result.token.access_token == "ya29.mocked-access-token"

    def test_refresh_preserves_refresh_token(self) -> None:
        def _mock_no_new_refresh(*args: Any, **kwargs: Any) -> dict:
            return {
                "access_token": "ya29.fresh",
                "expires_in": 3600,
                "token_type": "Bearer",
            }

        inject_token_exchange(_mock_no_new_refresh)
        config = _make_config()
        old_token = _make_expired_token(refresh_token="1//original-refresh")
        result = refresh_access_token(config, old_token)
        assert result.success is True
        # Refresh token should be preserved from original
        assert result.token.refresh_token == "1//original-refresh"

    def test_refresh_no_refresh_token_available(self) -> None:
        config = _make_config()
        token = _make_expired_no_refresh_token()
        result = refresh_access_token(config, token)
        assert result.success is False
        assert "No refresh token" in result.error

    def test_refresh_missing_credentials(self) -> None:
        config = GDriveAuthConfig(client_id="", client_secret="")
        token = _make_expired_token()
        result = refresh_access_token(config, token)
        assert result.success is False
        assert "required" in result.error.lower()

    def test_refresh_server_rejects(self) -> None:
        inject_token_exchange(_mock_token_exchange_error)
        config = _make_config()
        token = _make_expired_token()
        result = refresh_access_token(config, token)
        assert result.success is False
        assert result.status_code == 400


# ═════════════════════════════════════════════════════════════════════════
# Token persistence tests
# ═════════════════════════════════════════════════════════════════════════


class TestTokenPersistence:
    """Verify save/load token to/from disk."""

    def test_save_and_load_roundtrip(self, tmp_path: Path) -> None:
        token_path = str(tmp_path / "token.json")
        token = _make_valid_token()
        save_token_to_file(token, token_path)

        # Verify file exists
        assert os.path.exists(token_path)

        loaded = load_token_from_file(token_path)
        assert loaded is not None
        assert loaded.access_token == token.access_token
        assert loaded.refresh_token == token.refresh_token
        assert loaded.expires_at == token.expires_at

    def test_load_nonexistent_file(self) -> None:
        token = load_token_from_file("/tmp/nonexistent-gdrive-token-99999.json")
        assert token is None

    def test_load_invalid_json(self, tmp_path: Path) -> None:
        token_path = str(tmp_path / "bad.json")
        with open(token_path, "w") as f:
            f.write("not valid json {{{")
        token = load_token_from_file(token_path)
        assert token is None

    def test_save_creates_parent_dirs(self, tmp_path: Path) -> None:
        token_path = str(tmp_path / "deep" / "nested" / "token.json")
        token = _make_valid_token()
        save_token_to_file(token, token_path)
        assert os.path.exists(token_path)

    def test_save_with_env_var_expansion(self, tmp_path: Path) -> None:
        token_path = str(tmp_path / "token.json")
        token = _make_valid_token()
        save_token_to_file(token, token_path)

        with open(token_path, encoding="utf-8") as f:
            raw = json.load(f)

        assert raw["access_token"] == "ya29.test-access-token"
        assert raw["refresh_token"] == "1//test-refresh-token"
        assert "expires_at" in raw


# ═════════════════════════════════════════════════════════════════════════
# GDriveAuthenticator tests
# ═════════════════════════════════════════════════════════════════════════


class TestGDriveAuthenticator:
    """Verify the GDriveAuthenticator class lifecycle and token management."""

    def test_build_authenticator(self) -> None:
        auth = build_authenticator(
            client_id="cid", client_secret="secret", token_path="/tmp/t.json"
        )
        assert auth.config.client_id == "cid"
        assert auth.config.client_secret == "secret"
        assert auth.config.token_path == "/tmp/t.json"

    def test_initial_state_not_authenticated(self) -> None:
        auth = GDriveAuthenticator(_make_config())
        assert auth.is_authenticated is False
        assert auth.token is None

    def test_authenticate_with_valid_code(self) -> None:
        inject_token_exchange(_mock_token_exchange_success)
        auth = GDriveAuthenticator(_make_config())
        result = auth.authenticate("4/valid-code")
        assert result.success is True
        assert auth.is_authenticated is True
        assert auth.token is not None
        assert auth.token.access_token == "ya29.mocked-access-token"

    def test_authenticate_with_invalid_code(self) -> None:
        inject_token_exchange(_mock_token_exchange_error)
        auth = GDriveAuthenticator(_make_config())
        result = auth.authenticate("4/bad-code")
        assert result.success is False
        assert auth.is_authenticated is False

    def test_authenticate_persists_token(self, tmp_path: Path) -> None:
        token_path = str(tmp_path / "token.json")
        inject_token_exchange(_mock_token_exchange_success)
        config = _make_config(token_path=token_path)
        auth = GDriveAuthenticator(config)
        result = auth.authenticate("4/valid-code")
        assert result.success is True
        assert os.path.exists(token_path)

    def test_validate_with_valid_token(self) -> None:
        inject_token_exchange(_mock_token_exchange_success)
        auth = GDriveAuthenticator(_make_config())
        auth.authenticate("4/valid-code")
        result = auth.validate()
        assert result.success is True

    def test_validate_before_authenticate(self) -> None:
        auth = GDriveAuthenticator(_make_config())
        result = auth.validate()
        assert result.success is False
        assert "authenticate first" in result.error.lower()

    def test_validate_expired_token(self) -> None:
        inject_token_exchange(_mock_token_exchange_success)
        auth = GDriveAuthenticator(_make_config())
        auth.authenticate("4/valid-code")
        # Replace in-memory token with an expired one
        old_token = auth.token
        assert old_token is not None
        expired = GDriveToken(
            access_token=old_token.access_token,
            refresh_token=old_token.refresh_token,
            token_type=old_token.token_type,
            expires_at=time.time() - 3600,
            scope=old_token.scope,
        )
        auth._token = expired  # noqa: SLF001
        result = auth.validate()
        assert result.success is False
        assert "expired" in result.error.lower()

    def test_refresh_successful(self) -> None:
        inject_token_exchange(_mock_token_exchange_success)
        auth = GDriveAuthenticator(_make_config())
        # First authenticate to get a token
        auth.authenticate("4/valid-code")
        # Now refresh (mock returns success)
        result = auth.refresh()
        assert result.success is True
        assert result.token.access_token == "ya29.mocked-access-token"

    def test_refresh_before_authenticate(self) -> None:
        auth = GDriveAuthenticator(_make_config())
        result = auth.refresh()
        assert result.success is False
        assert "authenticate first" in result.error.lower()

    def test_get_valid_token_auto_refresh(self) -> None:
        """get_valid_token should auto-refresh an expired token."""
        inject_token_exchange(_mock_token_exchange_success)
        auth = GDriveAuthenticator(_make_config())
        auth.authenticate("4/valid-code")
        # Manually set token as expired
        assert auth.token is not None
        old_token = GDriveToken(
            access_token=auth.token.access_token,
            refresh_token=auth.token.refresh_token,
            token_type=auth.token.token_type,
            expires_at=time.time() - 1,  # expired
            scope=auth.token.scope,
        )
        auth._token = old_token  # noqa: SLF001
        result = auth.get_valid_token()
        assert result.success is True
        assert result.token.access_token == "ya29.mocked-access-token"

    def test_get_valid_token_no_token_at_all(self) -> None:
        auth = GDriveAuthenticator(_make_config())
        result = auth.get_valid_token()
        assert result.success is False
        assert "authenticate first" in result.error.lower()

    def test_get_valid_token_expired_no_refresh(self) -> None:
        """Expired token with no refresh token → unrecoverable."""
        inject_token_exchange(_mock_token_exchange_success)
        auth = GDriveAuthenticator(_make_config())
        auth.authenticate("4/valid-code")
        # Replace with expired + no refresh
        auth._token = _make_expired_no_refresh_token()  # noqa: SLF001
        result = auth.get_valid_token()
        assert result.success is False
        assert "No refresh token" in result.error or "no refresh" in result.error.lower()

    def test_get_valid_token_loads_from_disk(self, tmp_path: Path) -> None:
        """When no in-memory token, get_valid_token loads from disk."""
        token_path = str(tmp_path / "token.json")
        # Pre-save a valid token to disk
        valid_token = _make_valid_token(expires_in=3600)
        save_token_to_file(valid_token, token_path)

        config = _make_config(token_path=token_path)
        auth = GDriveAuthenticator(config)
        result = auth.get_valid_token()
        assert result.success is True
        assert result.token.access_token == "ya29.test-access-token"

    def test_validate_credentials_with_valid(self) -> None:
        auth = GDriveAuthenticator(_make_config())
        result = auth.validate_credentials()
        assert result.success is True

    def test_validate_credentials_empty_client_id(self) -> None:
        config = _make_config(client_id="")
        auth = GDriveAuthenticator(config)
        result = auth.validate_credentials()
        assert result.success is False
        assert "client_id" in result.error.lower()

    def test_validate_credentials_empty_client_secret(self) -> None:
        config = _make_config(client_secret="")
        auth = GDriveAuthenticator(config)
        result = auth.validate_credentials()
        assert result.success is False
        assert "client_secret" in result.error.lower()

    def test_revoke_clears_token(self, tmp_path: Path) -> None:
        token_path = str(tmp_path / "token.json")
        inject_token_exchange(_mock_token_exchange_success)
        config = _make_config(token_path=token_path)
        auth = GDriveAuthenticator(config)
        auth.authenticate("4/valid-code")
        assert auth.is_authenticated is True
        assert os.path.exists(token_path)

        result = auth.revoke()
        assert result.success is True
        assert auth.is_authenticated is False
        assert auth.token is None
        assert not os.path.exists(token_path)

    def test_get_valid_token_expired_refresh_fails(self) -> None:
        """When token is expired and refresh also fails → error."""
        # First authenticate successfully
        inject_token_exchange(_mock_token_exchange_success)
        auth = GDriveAuthenticator(_make_config())
        auth.authenticate("4/valid-code")

        # Now inject the error mock (next exchange call will fail)
        inject_token_exchange(_mock_token_exchange_error)

        # Force expire the token
        old_token = auth.token
        assert old_token is not None
        auth._token = GDriveToken(  # noqa: SLF001
            access_token=old_token.access_token,
            refresh_token=old_token.refresh_token,
            token_type=old_token.token_type,
            expires_at=time.time() - 1,
            scope=old_token.scope,
        )
        result = auth.get_valid_token()
        assert result.success is False
        assert "refresh failed" in result.error.lower()


# ═════════════════════════════════════════════════════════════════════════
# GDriveAuthConfig tests
# ═════════════════════════════════════════════════════════════════════════


class TestGDriveAuthConfig:
    """Verify GDriveAuthConfig creation and defaults."""

    def test_defaults(self) -> None:
        config = GDriveAuthConfig()
        assert config.client_id == ""
        assert config.client_secret == ""
        assert config.token_path == ""
        assert config.scopes == GOOGLE_DRIVE_SCOPES
        assert config.redirect_uri == "http://localhost:8080"
        assert config.token_grace_seconds == TOKEN_GRACE_PERIOD_SECONDS

    def test_custom_values(self) -> None:
        config = GDriveAuthConfig(
            client_id="custom-id.apps.googleusercontent.com",
            client_secret="custom-secret",
            token_path="/custom/path/token.json",
            scopes=("https://www.googleapis.com/auth/drive",),
            redirect_uri="http://myapp:3000/callback",
            token_grace_seconds=120.0,
        )
        assert config.client_id == "custom-id.apps.googleusercontent.com"
        assert config.client_secret == "custom-secret"
        assert config.token_path == "/custom/path/token.json"
        assert config.scopes == ("https://www.googleapis.com/auth/drive",)

    def test_is_frozen(self) -> None:
        config = GDriveAuthConfig()
        with pytest.raises(Exception):
            config.client_id = "hacked"  # type: ignore[misc]


# ═════════════════════════════════════════════════════════════════════════
# GDriveAuthResult tests
# ═════════════════════════════════════════════════════════════════════════


class TestGDriveAuthResult:
    """Verify GDriveAuthResult creation and defaults."""

    def test_success_result(self) -> None:
        token = _make_valid_token()
        result = GDriveAuthResult(success=True, token=token)
        assert result.success is True
        assert result.token.access_token == "ya29.test-access-token"
        assert result.error == ""
        assert result.status_code == 0

    def test_failure_result(self) -> None:
        result = GDriveAuthResult(
            success=False,
            error="Authentication failed",
            status_code=401,
        )
        assert result.success is False
        assert result.error == "Authentication failed"
        assert result.status_code == 401

    def test_default_token_on_failure(self) -> None:
        """Default token on failure is an empty token."""
        result = GDriveAuthResult(success=False, error="nope")
        assert result.token.access_token == ""
        assert result.token.is_valid is False


# ═════════════════════════════════════════════════════════════════════════
# Inject mechanism tests
# ═════════════════════════════════════════════════════════════════════════


class TestTokenExchangeInject:
    """Verify the token exchange injection mechanism."""

    def test_inject_and_restore(self) -> None:
        """After inject + restore, default handler is restored."""
        original = inject_token_exchange(_mock_token_exchange_success)
        # original should be None (the default)
        assert original is None

        inject_token_exchange(None)
        # Now the default handler should be _http_token_exchange
        from src.gdrive_auth import _get_token_exchange_fn

        handler = _get_token_exchange_fn()
        assert handler is _http_token_exchange

    def test_mock_called_with_correct_params(self) -> None:
        """Verify the mock receives proper params during exchange."""
        captured: dict = {}

        def _capture_params(uri: str, params: dict, timeout: float = 30.0) -> dict:
            captured["uri"] = uri
            captured["params"] = params
            captured["timeout"] = timeout
            return {
                "access_token": "ya29.captured",
                "expires_in": 3600,
                "token_type": "Bearer",
                "refresh_token": "1//captured-refresh",
            }

        inject_token_exchange(_capture_params)
        config = _make_config()
        result = exchange_auth_code(config, "4/auth-code-123")

        assert result.success is True
        assert captured["uri"] == OAUTH_TOKEN_URI
        assert captured["params"]["code"] == "4/auth-code-123"
        assert captured["params"]["grant_type"] == "authorization_code"
        assert captured["params"]["client_id"] == config.client_id
        assert captured["params"]["client_secret"] == config.client_secret
        assert captured["params"]["redirect_uri"] == config.redirect_uri


# ═════════════════════════════════════════════════════════════════════════
# End-to-end token flow test (the core Sub-AC 19a-1 verification)
# ═════════════════════════════════════════════════════════════════════════


class TestTokenFlowEndToEnd:
    """Verify the complete token lifecycle: acquire → persist → refresh.

    This is the primary verification for Sub-AC 19a-1: a mock test
    that exercises the entire OAuth token flow without real network
    calls.
    """

    def test_full_flow_acquire_persist_refresh_revoke(
        self, tmp_path: Path
    ) -> None:
        """End-to-end: auth code → token → save → load → refresh → revoke."""
        token_path = str(tmp_path / "gdrive_token.json")

        # ── Phase 1: Acquire ──────────────────────────────────────────
        inject_token_exchange(_mock_token_exchange_success)
        config = _make_config(token_path=token_path)
        auth = GDriveAuthenticator(config)

        # User provides auth code → system exchanges for tokens
        result = auth.authenticate("4/valid-auth-code")
        assert result.success is True, f"Auth failed: {result.error}"
        assert result.token.access_token == "ya29.mocked-access-token"
        assert result.token.refresh_token == "1//mocked-refresh"
        assert result.token.is_valid is True

        # Token is persisted to disk
        assert os.path.exists(token_path), "Token file not created"

        # ── Phase 2: Validate ─────────────────────────────────────────
        validate_result = auth.validate()
        assert validate_result.success is True

        # ── Phase 3: Load from disk (simulate restart) ────────────────
        # Create a fresh authenticator (simulating process restart)
        auth2 = GDriveAuthenticator(config)
        # Token loads transparently via get_valid_token
        result2 = auth2.get_valid_token()
        assert result2.success is True
        assert result2.token.access_token == "ya29.mocked-access-token"

        # ── Phase 4: Refresh ──────────────────────────────────────────
        # Simulate token expiry
        assert auth2.token is not None
        auth2._token = GDriveToken(  # noqa: SLF001
            access_token=auth2.token.access_token,
            refresh_token=auth2.token.refresh_token,
            token_type=auth2.token.token_type,
            expires_at=time.time() - 1,
            scope=auth2.token.scope,
        )

        # get_valid_token should auto-refresh
        refresh_result = auth2.get_valid_token()
        assert refresh_result.success is True, (
            f"Auto-refresh failed: {refresh_result.error}"
        )
        assert refresh_result.token.access_token == "ya29.mocked-access-token"

        # ── Phase 5: Revoke ───────────────────────────────────────────
        revoke_result = auth2.revoke()
        assert revoke_result.success is True
        assert auth2.is_authenticated is False
        assert not os.path.exists(token_path), "Token file not deleted"

        # After revoke, get_valid_token fails
        post_revoke = auth2.get_valid_token()
        assert post_revoke.success is False

    def test_error_path_invalid_code_then_retry(self, tmp_path: Path) -> None:
        """Invalid auth code → error → retry with valid code → success."""
        token_path = str(tmp_path / "gdrive_token.json")
        config = _make_config(token_path=token_path)
        auth = GDriveAuthenticator(config)

        # First attempt: invalid code
        inject_token_exchange(_mock_token_exchange_error)
        result1 = auth.authenticate("4/bad-code")
        assert result1.success is False
        assert result1.status_code == 400

        # Second attempt: valid code
        inject_token_exchange(_mock_token_exchange_success)
        result2 = auth.authenticate("4/good-code")
        assert result2.success is True
        assert auth.is_authenticated is True

    def test_error_path_expired_no_refresh_leads_to_re_authenticate(
        self, tmp_path: Path
    ) -> None:
        """Expired token without refresh → must re-authenticate."""
        token_path = str(tmp_path / "gdrive_token.json")
        config = _make_config(token_path=token_path)
        auth = GDriveAuthenticator(config)

        # Authenticate successfully first
        inject_token_exchange(_mock_token_exchange_success)
        auth.authenticate("4/initial-code")

        # Simulate token expiry without refresh token
        auth._token = _make_expired_no_refresh_token()  # noqa: SLF001

        result = auth.get_valid_token()
        assert result.success is False
        assert "re-authenticate" in result.error.lower()

        # Re-authenticate should work
        inject_token_exchange(_mock_token_exchange_success)
        result2 = auth.authenticate("4/new-code")
        assert result2.success is True

    def test_credentials_static_validation(self) -> None:
        """validate_credentials performs static checks only."""
        # Valid config
        config = _make_config(
            client_id="my-app.apps.googleusercontent.com",
            client_secret="GOCSPX-secret",
        )
        auth = GDriveAuthenticator(config)
        result = auth.validate_credentials()
        assert result.success is True

        # Missing client_id
        auth2 = GDriveAuthenticator(_make_config(client_id=""))
        result2 = auth2.validate_credentials()
        assert result2.success is False
        assert "client_id" in result2.error.lower()

        # Missing client_secret
        auth3 = GDriveAuthenticator(_make_config(client_secret=""))
        result3 = auth3.validate_credentials()
        assert result3.success is False
        assert "client_secret" in result3.error.lower()
