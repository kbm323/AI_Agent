"""Google Drive OAuth authentication module.

Sub-AC 19a-1: Establishes OAuth connection to Google Drive, validates
credentials, and manages the token lifecycle (acquire, refresh, persist,
validate).  Designed for the OpenClaw tool-use executor to access
Google Drive for meeting artifact storage and retrieval.

Supports the standard OAuth 2.0 authorization code flow for web/desktop
applications.  Token state is persisted to a local JSON file so that
long-running meeting pipelines survive process restarts.

Usage::

    from src.gdrive_auth import GDriveAuthenticator, GDriveAuthConfig

    config = GDriveAuthConfig(
        client_id="...",
        client_secret="...",
        token_path="/path/to/token.json",
    )
    auth = GDriveAuthenticator(config)
    token = auth.get_valid_token()   # auto-refresh if expired
    # Use token.access_token in Google Drive API calls
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Optional


# ── Constants ────────────────────────────────────────────────────────────

DEFAULT_TOKEN_DIR = ".gdrive"
"""Default directory for storing Google Drive token files."""

DEFAULT_TOKEN_FILENAME = "token.json"
"""Default filename for the serialized token."""

GOOGLE_DRIVE_SCOPES: tuple[str, ...] = (
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/drive.readonly",
)
"""Default OAuth scopes for Google Drive access.

``drive.file`` — per-file access to files created/opened by the app.
``drive.readonly`` — read metadata and content for all Drive files.
"""

TOKEN_GRACE_PERIOD_SECONDS: float = 300.0
"""Seconds before actual expiry to consider a token expired.

Refreshing early avoids 401 errors in the middle of API operations."""

OAUTH_TOKEN_URI = "https://oauth2.googleapis.com/token"
"""Google OAuth 2.0 token endpoint."""

MAX_RETRY_AUTH_ATTEMPTS: int = 2
"""Maximum attempts for token refresh before treating as failure."""


# ── Data types ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class GDriveAuthConfig:
    """Configuration for Google Drive OAuth authentication.

    Attributes:
        client_id: Google Cloud OAuth 2.0 client ID.
        client_secret: Google Cloud OAuth 2.0 client secret.
        token_path: Filesystem path for token persistence.
        scopes: OAuth scopes to request (defaults to drive.file + drive.readonly).
        redirect_uri: OAuth redirect URI (default: localhost flow).
        token_grace_seconds: Refresh token this many seconds before expiry.
    """

    client_id: str = ""
    client_secret: str = ""
    token_path: str = ""
    scopes: tuple[str, ...] = GOOGLE_DRIVE_SCOPES
    redirect_uri: str = "http://localhost:8080"
    token_grace_seconds: float = TOKEN_GRACE_PERIOD_SECONDS

    def __post_init__(self) -> None:
        if self.client_id and not isinstance(self.client_id, str):
            raise TypeError("client_id must be a string")
        if self.client_secret and not isinstance(self.client_secret, str):
            raise TypeError("client_secret must be a string")


@dataclass(frozen=True)
class GDriveToken:
    """Serializable OAuth 2.0 token for Google Drive.

    Attributes:
        access_token: The short-lived bearer token for API calls.
        refresh_token: Long-lived token used to obtain new access tokens.
        token_type: Usually ``"Bearer"``.
        expires_at: Absolute Unix timestamp when the token expires.
        scope: Space-separated list of granted scopes.
        raw: Complete token response dictionary (for forward compatibility).
    """

    access_token: str
    refresh_token: str = ""
    token_type: str = "Bearer"
    expires_at: float = 0.0
    scope: str = ""
    raw: dict = field(default_factory=dict)

    @property
    def is_expired(self) -> bool:
        """True when the token has passed its expiry plus grace period."""
        if self.expires_at <= 0:
            return True
        return time.time() >= (self.expires_at - TOKEN_GRACE_PERIOD_SECONDS)

    @property
    def is_valid(self) -> bool:
        """True when the token exists, is not empty, and has not expired."""
        return bool(self.access_token) and not self.is_expired

    @property
    def can_refresh(self) -> bool:
        """True when a refresh token is available for renewal."""
        return bool(self.refresh_token)

    def to_dict(self) -> dict:
        """Serialize to a JSON-compatible dictionary."""
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "token_type": self.token_type,
            "expires_at": self.expires_at,
            "scope": self.scope,
            "raw": self.raw,
        }

    @classmethod
    def from_dict(cls, data: dict) -> GDriveToken:
        """Deserialize from a dictionary (e.g. parsed JSON)."""
        return cls(
            access_token=data.get("access_token", ""),
            refresh_token=data.get("refresh_token", ""),
            token_type=data.get("token_type", "Bearer"),
            expires_at=float(data.get("expires_at", 0)),
            scope=data.get("scope", ""),
            raw=data.get("raw", {}),
        )

    @classmethod
    def from_oauth_response(cls, response: dict, refresh_token: str = "") -> GDriveToken:
        """Build a token from a Google OAuth token endpoint response.

        Args:
            response: The JSON-decoded response from the token endpoint.
            refresh_token: Optional refresh token (preserved from prior auth).
        """
        expires_in = float(response.get("expires_in", 3600))
        expires_at = time.time() + expires_in
        return cls(
            access_token=response.get("access_token", ""),
            refresh_token=response.get("refresh_token", refresh_token),
            token_type=response.get("token_type", "Bearer"),
            expires_at=expires_at,
            scope=response.get("scope", ""),
            raw={**response},
        )


@dataclass(frozen=True)
class GDriveAuthResult:
    """Outcome of an authentication or token validation operation.

    Attributes:
        success: True when authentication succeeded and a valid token is available.
        token: The resulting ``GDriveToken`` if successful, or an empty token.
        error: Human-readable error description on failure.
        status_code: HTTP status code mirror (0 for non-HTTP errors).
        execution_id: Correlates this result with execution tracking.
    """

    success: bool
    token: GDriveToken = field(
        default_factory=lambda: GDriveToken(access_token="")
    )
    error: str = ""
    status_code: int = 0
    execution_id: str = ""


# ── Token persistence ────────────────────────────────────────────────────


def _ensure_token_dir(path: str) -> None:
    """Create the parent directory for the token file if it does not exist."""
    token_path = Path(path)
    parent = token_path.parent
    if parent and not parent.exists():
        parent.mkdir(parents=True, exist_ok=True)


def load_token_from_file(path: str) -> GDriveToken | None:
    """Load a serialized ``GDriveToken`` from a JSON file.

    Returns:
        The deserialized token, or ``None`` if the file does not exist
        or cannot be parsed.
    """
    resolved = Path(os.path.expandvars(os.path.expanduser(path)))
    if not resolved.exists():
        return None

    try:
        with open(resolved, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None

    if not data or not isinstance(data, dict):
        return None

    return GDriveToken.from_dict(data)


def save_token_to_file(token: GDriveToken, path: str) -> None:
    """Persist a ``GDriveToken`` to a JSON file.

    Creates parent directories as needed.  The file is written atomically
    via a temporary file + rename to avoid corruption on crash.
    """
    _ensure_token_dir(path)
    resolved = Path(os.path.expandvars(os.path.expanduser(path)))
    tmp_path = resolved.with_suffix(resolved.suffix + ".tmp")

    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(token.to_dict(), f, ensure_ascii=False, indent=2)

    os.replace(tmp_path, resolved)


# ── Token validation ─────────────────────────────────────────────────────


def is_token_valid(token: GDriveToken) -> bool:
    """Check whether a ``GDriveToken`` is non-empty and not expired.

    Returns:
        True if the token has an access_token and has not expired.
    """
    return token.is_valid


def token_ttl_seconds(token: GDriveToken) -> float:
    """Return the remaining time-to-live in seconds before expiration.

    Returns:
        Seconds remaining (always >= 0).  0 means already expired.
    """
    if token.expires_at <= 0:
        return 0.0
    remaining = token.expires_at - time.time()
    return max(0.0, remaining)


# ── Raw token exchange (mock-injectable) ─────────────────────────────────

#: Injectable function for the actual HTTP token exchange.
#: Defaults to ``_http_token_exchange`` which uses urllib.
#: Tests inject ``_mock_token_exchange`` to verify the flow without
#: real network calls.
_token_exchange_fn = None


def _get_token_exchange_fn():
    """Return the current token exchange function (real or mock)."""
    global _token_exchange_fn
    if _token_exchange_fn is not None:
        return _token_exchange_fn
    return _http_token_exchange


def inject_token_exchange(handler) -> None:
    """Replace the token exchange handler (for testing).

    Args:
        handler: A callable with signature
                 ``(token_uri, params, timeout) -> dict``, or ``None``
                 to restore the default HTTP handler.
    """
    global _token_exchange_fn
    _token_exchange_fn = handler


def _http_token_exchange(
    token_uri: str,
    params: dict,
    timeout: float = 30.0,
) -> dict:
    """Perform an HTTP POST to the OAuth token endpoint.

    Uses only the standard library (urllib) to avoid external
    dependencies.  Returns the JSON-decoded response dict.

    Raises:
        OSError: On network failure.
        json.JSONDecodeError: On non-JSON response.
        ValueError: On HTTP error status.
    """
    import urllib.error
    import urllib.parse
    import urllib.request

    data = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(
        token_uri,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body)
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8") if e.fp else ""
        try:
            error_data: dict = json.loads(error_body)
        except json.JSONDecodeError:
            error_data = {"error": error_body}
        error_data["_http_status"] = e.code
        return error_data
    except urllib.error.URLError as e:
        raise OSError(f"Token exchange network error: {e.reason}") from e


# ── Token refresh ────────────────────────────────────────────────────────


def refresh_access_token(
    config: GDriveAuthConfig,
    token: GDriveToken,
    *,
    timeout: float = 30.0,
) -> GDriveAuthResult:
    """Refresh an expired access token using the refresh token.

    Sends a POST to Google's OAuth token endpoint with
    ``grant_type=refresh_token``.  On success, returns a new
    ``GDriveToken`` with an updated access_token and expires_at.

    The refresh token in the response is preserved from the input
    (Google only returns a new refresh token when the old one is
    rotated, which is rare).

    Args:
        config: The GDriveAuthConfig with client credentials.
        token: The current token (must have a refresh_token).
        timeout: HTTP request timeout in seconds.

    Returns:
        ``GDriveAuthResult`` with ``.success=True`` and a fresh token,
        or ``.success=False`` with an error description.
    """
    if not config.client_id or not config.client_secret:
        return GDriveAuthResult(
            success=False,
            error="Client ID and Client Secret are required for token refresh",
            status_code=0,
        )

    if not token.can_refresh:
        return GDriveAuthResult(
            success=False,
            error="No refresh token available — re-authentication required",
            status_code=0,
        )

    exchange_fn = _get_token_exchange_fn()

    params = {
        "client_id": config.client_id,
        "client_secret": config.client_secret,
        "refresh_token": token.refresh_token,
        "grant_type": "refresh_token",
    }

    try:
        response = exchange_fn(OAUTH_TOKEN_URI, params, timeout)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return GDriveAuthResult(
            success=False,
            error=f"Token refresh failed: {exc}",
            status_code=0,
        )

    http_status = response.pop("_http_status", 0)
    if http_status >= 400 or "error" in response:
        error_desc = response.get("error_description", response.get("error", "unknown"))
        return GDriveAuthResult(
            success=False,
            error=f"Token refresh rejected ({http_status}): {error_desc}",
            status_code=http_status,
        )

    if "access_token" not in response:
        return GDriveAuthResult(
            success=False,
            error="Token refresh response missing access_token",
            status_code=http_status,
        )

    new_token = GDriveToken.from_oauth_response(
        response,
        refresh_token=token.refresh_token,
    )

    return GDriveAuthResult(success=True, token=new_token)


# ── Authorization code exchange ──────────────────────────────────────────


def exchange_auth_code(
    config: GDriveAuthConfig,
    auth_code: str,
    *,
    timeout: float = 30.0,
) -> GDriveAuthResult:
    """Exchange an OAuth authorization code for access and refresh tokens.

    This is the second step of the OAuth 2.0 authorization code flow.
    The user first visits a Google consent URL, obtains an authorization
    code, and this function exchanges it for real tokens.

    Args:
        config: The GDriveAuthConfig with client credentials and redirect URI.
        auth_code: The one-time authorization code from Google.
        timeout: HTTP request timeout in seconds.

    Returns:
        ``GDriveAuthResult`` with a fresh ``GDriveToken`` on success.
    """
    if not auth_code:
        return GDriveAuthResult(
            success=False,
            error="Authorization code is empty",
            status_code=0,
        )

    if not config.client_id or not config.client_secret:
        return GDriveAuthResult(
            success=False,
            error="Client ID and Client Secret are required",
            status_code=0,
        )

    exchange_fn = _get_token_exchange_fn()

    params = {
        "client_id": config.client_id,
        "client_secret": config.client_secret,
        "code": auth_code,
        "grant_type": "authorization_code",
        "redirect_uri": config.redirect_uri,
    }

    try:
        response = exchange_fn(OAUTH_TOKEN_URI, params, timeout)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return GDriveAuthResult(
            success=False,
            error=f"Authorization code exchange failed: {exc}",
            status_code=0,
        )

    http_status = response.pop("_http_status", 0)
    if http_status >= 400 or "error" in response:
        error_desc = response.get("error_description", response.get("error", "unknown"))
        return GDriveAuthResult(
            success=False,
            error=f"Code exchange rejected ({http_status}): {error_desc}",
            status_code=http_status,
        )

    if "access_token" not in response:
        return GDriveAuthResult(
            success=False,
            error="Authorization response missing access_token",
            status_code=http_status,
        )

    token = GDriveToken.from_oauth_response(response)
    return GDriveAuthResult(success=True, token=token)


# ── Main authenticator class ─────────────────────────────────────────────


class GDriveAuthenticator:
    """Google Drive OAuth authenticator for the meeting system.

    Manages the complete token lifecycle: acquisition via authorization
    code, persistence to disk, validation, and refresh.

    Usage::

        config = GDriveAuthConfig(
            client_id="xxx.apps.googleusercontent.com",
            client_secret="GOCSPX-xxx",
            token_path="~/.gdrive/token.json",
        )
        auth = GDriveAuthenticator(config)

        # First time — exchange an auth code from the user
        result = auth.authenticate(auth_code="4/0AanRRr...")
        if result.success:
            print("Authenticated!")

        # Subsequent calls — auto-refresh if needed
        token = auth.get_valid_token()
        print(f"Access token: {token.access_token[:10]}...")

    Token state is persisted to ``config.token_path``.  After the
    initial authorization, subsequent ``get_valid_token()`` calls
    reload from disk and auto-refresh when expired.
    """

    def __init__(self, config: GDriveAuthConfig) -> None:
        self._config = config
        self._token: GDriveToken | None = None

    # ── Properties ───────────────────────────────────────────────────

    @property
    def config(self) -> GDriveAuthConfig:
        return self._config

    @property
    def token(self) -> GDriveToken | None:
        return self._token

    @property
    def is_authenticated(self) -> bool:
        """True when a valid, non-expired token is loaded."""
        return self._token is not None and self._token.is_valid

    # ── Authentication ───────────────────────────────────────────────

    def authenticate(self, auth_code: str) -> GDriveAuthResult:
        """Complete the OAuth 2.0 authorization code flow.

        Exchanges the *auth_code* for access + refresh tokens, persists
        the token to disk, and stores it in memory for subsequent calls.

        Args:
            auth_code: The one-time authorization code from Google.

        Returns:
            ``GDriveAuthResult`` indicating success or failure.
        """
        result = exchange_auth_code(self._config, auth_code)

        if result.success:
            self._token = result.token
            if self._config.token_path:
                save_token_to_file(result.token, self._config.token_path)

        return result

    def load_token_from_disk(self) -> GDriveToken | None:
        """Load a previously saved token from ``config.token_path``.

        Returns:
            The deserialized token, or ``None`` if no saved token exists.
        """
        if not self._config.token_path:
            return None

        token = load_token_from_file(self._config.token_path)
        if token is not None:
            self._token = token
        return token

    # ── Token validation ─────────────────────────────────────────────

    def validate(self) -> GDriveAuthResult:
        """Validate the currently held token.

        Checks that the token is non-empty and not expired.  Does NOT
        perform a network call to Google.

        Returns:
            ``GDriveAuthResult`` with ``.success=True`` when the token
            is present and not expired.
        """
        if self._token is None:
            return GDriveAuthResult(
                success=False,
                error="No token loaded — authenticate first",
                status_code=0,
            )

        if not self._token.access_token:
            return GDriveAuthResult(
                success=False,
                error="Token has empty access_token",
                status_code=0,
            )

        if self._token.is_expired:
            return GDriveAuthResult(
                success=False,
                error=(
                    f"Token expired at {datetime.fromtimestamp(self._token.expires_at, UTC).isoformat()}"
                ),
                status_code=0,
            )

        return GDriveAuthResult(
            success=True,
            token=self._token,
        )

    # ── Token refresh ────────────────────────────────────────────────

    def refresh(self) -> GDriveAuthResult:
        """Refresh the access token using the stored refresh token.

        Returns:
            ``GDriveAuthResult`` with a fresh token on success.
            On failure, the old token is NOT cleared — callers can retry
            or escalate.
        """
        if self._token is None:
            return GDriveAuthResult(
                success=False,
                error="No token to refresh — authenticate first",
                status_code=0,
            )

        result = refresh_access_token(self._config, self._token)

        if result.success:
            self._token = result.token
            if self._config.token_path:
                save_token_to_file(result.token, self._config.token_path)

        return result

    # ── High-level: get a valid token ────────────────────────────────

    def get_valid_token(self) -> GDriveAuthResult:
        """Get a valid access token, refreshing if necessary.

        This is the main entry point for callers that need a token
        for Google Drive API calls.  The logic is:

        1. If no token is loaded, try loading from disk.
        2. If still no token, return failure.
        3. If the token is valid, return it.
        4. If expired and a refresh token is available, refresh it.
        5. If refresh fails or no refresh token, return failure.

        Returns:
            ``GDriveAuthResult`` with a valid ``GDriveToken`` on success.
        """
        # Step 1: Load from disk if not already loaded
        if self._token is None:
            self.load_token_from_disk()

        # Step 2: Still no token
        if self._token is None:
            return GDriveAuthResult(
                success=False,
                error="No token available — authenticate first",
                status_code=0,
            )

        # Step 3: Token is still valid
        if not self._token.is_expired:
            return GDriveAuthResult(
                success=True,
                token=self._token,
            )

        # Step 4: Expired — try refresh
        if self._token.can_refresh:
            refresh_result = self.refresh()
            if refresh_result.success:
                return refresh_result
            return GDriveAuthResult(
                success=False,
                error=f"Token expired and refresh failed: {refresh_result.error}",
                status_code=refresh_result.status_code,
                token=self._token,  # return the stale token for diagnostics
            )

        # Step 5: Expired and no refresh token
        return GDriveAuthResult(
            success=False,
            error="Token expired and no refresh token available — re-authenticate",
            status_code=0,
            token=self._token,
        )

    # ── Credential validation (static check) ─────────────────────────

    def validate_credentials(self) -> GDriveAuthResult:
        """Validate that client credentials are configured correctly.

        Checks that client_id and client_secret are non-empty and
        follow expected formats (Google OAuth client ID patterns).

        This is a static check — no network calls are made.

        Returns:
            ``GDriveAuthResult`` indicating whether credentials
            appear valid.
        """
        errors: list[str] = []

        if not self._config.client_id:
            errors.append("client_id is empty")
        elif not self._config.client_id.endswith(".apps.googleusercontent.com"):
            # Not a hard error (some projects use different formats)
            # but worth noting
            pass

        if not self._config.client_secret:
            errors.append("client_secret is empty")

        if errors:
            return GDriveAuthResult(
                success=False,
                error="; ".join(errors),
                status_code=0,
            )

        return GDriveAuthResult(
            success=True,
            error="Credentials appear valid (static check only)",
        )

    # ── Convenience: revocation placeholder ──────────────────────────

    def revoke(self) -> GDriveAuthResult:
        """Revoke the current token and clear the stored file.

        This invalidates the token locally.  A full Google revocation
        requires an HTTP call to the revocation endpoint, but that is
        deferred to a future AC (token lifecycle management).

        Returns:
            ``GDriveAuthResult`` indicating the local state was cleared.
        """
        self._token = None
        if self._config.token_path:
            resolved = Path(
                os.path.expandvars(os.path.expanduser(self._config.token_path))
            )
            if resolved.exists():
                resolved.unlink()
        return GDriveAuthResult(
            success=True,
            error="Token cleared locally (server revocation not performed)",
        )


# ── Module-level convenience ─────────────────────────────────────────────


def build_authenticator(
    client_id: str = "",
    client_secret: str = "",
    token_path: str = "",
) -> GDriveAuthenticator:
    """Build a ``GDriveAuthenticator`` from individual parameters.

    Accepts empty strings for all parameters — useful when credentials
    are loaded from environment variables or a config file later.

    Args:
        client_id: Google OAuth client ID.
        client_secret: Google OAuth client secret.
        token_path: Path for token persistence.

    Returns:
        A configured ``GDriveAuthenticator`` instance.
    """
    config = GDriveAuthConfig(
        client_id=client_id,
        client_secret=client_secret,
        token_path=token_path,
    )
    return GDriveAuthenticator(config)
