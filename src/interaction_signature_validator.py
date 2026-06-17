"""Interaction Signature Validator — Sub-AC 1.1.2

Verifies Ed25519 signatures on incoming Discord interaction HTTP requests
using the application's public key.  Rejects invalid, tampered, or replayed
requests *before* any payload processing or JSON parsing takes place.

This module is the security boundary between Discord's outgoing webhooks and
the AI_Agent meeting orchestration system.  It implements the verification
algorithm documented at:

    https://discord.com/developers/docs/interactions/receiving-and-responding#security-and-authorization

Design decisions
----------------
- **Standalone**: no dependency on other AI_Agent modules — only requires
  PyNaCl (``nacl``).  This keeps the security boundary auditable in
  isolation.
- **Before-parse enforcement**: signature verification must complete
  successfully before the raw body bytes are decoded as UTF-8 or
  deserialized from JSON.  Callers wire this module *in front of*
  ``discord_interaction_parser.py``.
- **Structured results**: every call returns a ``SignatureResult`` —
  never ``None``, never an unhandled exception.  Callers branch on
  ``.valid``.
- **Hex-encoded keys**: public key and signature follow Discord's wire
  format (hex strings).  Internally decoded to bytes for PyNaCl.
- **Timing-safe**: uses PyNaCl's ``VerifyKey.verify()`` which
  internally calls ``libsodium``'s ``crypto_sign_verify_detached`` —
  a constant-time implementation.

Usage
-----
::

    from src.interaction_signature_validator import (
        verify_request_signature,
        SignatureResult,
    )

    result = verify_request_signature(
        raw_body=request_body_bytes,
        signature_hex=headers["X-Signature-Ed25519"],
        timestamp=headers["X-Signature-Timestamp"],
        public_key_hex=os.environ["DISCORD_PUBLIC_KEY"],
    )

    if not result.valid:
        # Reject — log result.error, return HTTP 401
        ...

    # Signature valid — proceed to payload parsing
    from src.discord_interaction_parser import parse_interaction_command
    ...

Testability
-----------
Testable with known key pairs and sample payloads — no network, no
Discord application registration required.  See
``tests/test_interaction_signature_validator.py``.
"""

from __future__ import annotations

from dataclasses import dataclass

import nacl.encoding
import nacl.exceptions
import nacl.signing

# ── Public key constants ────────────────────────────────────────────────

PUBLIC_KEY_BYTE_LENGTH: int = 32
"""Ed25519 public key length in bytes (256 bits)."""

SIGNATURE_BYTE_LENGTH: int = 64
"""Ed25519 signature length in bytes (512 bits)."""


# ── Result types ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SignatureResult:
    """The outcome of a signature verification call.

    Pattern: every call to :func:`verify_request_signature` returns
    exactly one ``SignatureResult``.  Callers inspect ``.valid`` and
    branch accordingly.

    When ``valid`` is True:
      - The request body, timestamp, and signature are cryptographically
        consistent with the provided public key.  The caller may proceed
        to payload parsing.

    When ``valid`` is False:
      - ``.error`` holds a human-readable description.
      - ``.error_code`` holds a machine-readable code for structured
        logging and monitoring.
    """

    valid: bool
    """True when the Ed25519 signature cryptographically verifies."""

    error: str = ""
    """Human-readable error description (only populated when valid=False)."""

    error_code: str = ""
    """Machine-readable error code for logging / monitoring.

    Possible values:

    - ``MISSING_PARAMETER`` — one or more required parameters are empty.
    - ``INVALID_KEY_HEX`` — public key is not valid hex.
    - ``BAD_KEY_LENGTH`` — public key has wrong byte length.
    - ``INVALID_SIGNATURE_HEX`` — signature is not valid hex.
    - ``BAD_SIGNATURE_LENGTH`` — signature has wrong byte length.
    - ``VERIFICATION_FAILED`` — cryptographic verification failed
      (tampered body, wrong key, reply attack, etc.).
    """


# ── Public API ───────────────────────────────────────────────────────────


def verify_request_signature(
    raw_body: bytes,
    signature_hex: str,
    timestamp: str,
    public_key_hex: str,
) -> SignatureResult:
    """Verify an Ed25519 Discord interaction request signature.

    This is the single entry point for Sub-AC 1.1.2.  It implements the
    verification algorithm described in Discord's documentation:

    1. Decode the hex-encoded public key (must be exactly 32 bytes).
    2. Decode the hex-encoded signature (must be exactly 64 bytes).
    3. Reconstruct the signed message as ``timestamp`` (UTF-8) +
       ``raw_body`` (raw bytes).
    4. Verify using the Ed25519 verify key via PyNaCl / libsodium.

    Args:
        raw_body: The raw HTTP request body bytes — MUST be the exact
                  bytes Discord sent.  Do NOT decode to string or parse
                  as JSON before calling this function.
        signature_hex: Value of the ``X-Signature-Ed25519`` HTTP header
                       (hex-encoded 64-byte Ed25519 signature).
        timestamp: Value of the ``X-Signature-Timestamp`` HTTP header
                   (ASCII decimal string, e.g. ``"1701234567"``).
        public_key_hex: The Discord application's public key from the
                        Developer Portal (hex-encoded 32-byte key).

    Returns:
        ``SignatureResult`` — inspect ``.valid``:
        - ``True`` → the request is cryptographically authentic.
        - ``False`` → ``.error`` and ``.error_code`` explain why.

    Examples:
        Generate a known key pair for testing::

            >>> import nacl.signing, nacl.encoding
            >>> sk = nacl.signing.SigningKey.generate()
            >>> vk_hex = sk.verify_key.encode(nacl.encoding.HexEncoder).decode()
            >>> sk_hex = sk.encode(nacl.encoding.HexEncoder).decode()
            >>> body = b'{"type":1}'
            >>> ts = "1234567890"
            >>> msg = ts.encode() + body
            >>> sig_hex = sk.sign(msg).signature.hex()

        Verify a valid signature::

            >>> result = verify_request_signature(body, sig_hex, ts, vk_hex)
            >>> result.valid
            True

        Tampered body fails::

            >>> result = verify_request_signature(b'{"type":2}', sig_hex, ts, vk_hex)
            >>> result.valid
            False
            >>> result.error_code
            'VERIFICATION_FAILED'
    """
    # ── Guard: all parameters must be provided ──────────────────
    if not signature_hex.strip() or not timestamp.strip() or not public_key_hex.strip():
        missing: list[str] = []
        if not signature_hex:
            missing.append("signature_hex")
        if not timestamp:
            missing.append("timestamp")
        if not public_key_hex:
            missing.append("public_key_hex")
        return SignatureResult(
            valid=False,
            error=f"Missing required parameter(s): {', '.join(missing)}",
            error_code="MISSING_PARAMETER",
        )

    # ── Guard: raw_body must not be empty ───────────────────────
    if not raw_body:
        return SignatureResult(
            valid=False,
            error="Raw body is empty — nothing to verify",
            error_code="EMPTY_BODY",
        )

    # ── Decode public key (32 bytes hex → bytes) ────────────────
    try:
        public_key_bytes = bytes.fromhex(public_key_hex)
    except ValueError:
        return SignatureResult(
            valid=False,
            error="Public key is not valid hexadecimal",
            error_code="INVALID_KEY_HEX",
        )

    if len(public_key_bytes) != PUBLIC_KEY_BYTE_LENGTH:
        return SignatureResult(
            valid=False,
            error=(
                f"Public key must be {PUBLIC_KEY_BYTE_LENGTH} bytes "
                f"(got {len(public_key_bytes)})"
            ),
            error_code="BAD_KEY_LENGTH",
        )

    # ── Decode signature (64 bytes hex → bytes) ─────────────────
    try:
        signature_bytes = bytes.fromhex(signature_hex)
    except ValueError:
        return SignatureResult(
            valid=False,
            error="Signature is not valid hexadecimal",
            error_code="INVALID_SIGNATURE_HEX",
        )

    if len(signature_bytes) != SIGNATURE_BYTE_LENGTH:
        return SignatureResult(
            valid=False,
            error=(
                f"Ed25519 signature must be {SIGNATURE_BYTE_LENGTH} bytes "
                f"(got {len(signature_bytes)})"
            ),
            error_code="BAD_SIGNATURE_LENGTH",
        )

    # ── Reconstruct the signed message ──────────────────────────
    message = timestamp.encode("utf-8") + raw_body

    # ── Verify ──────────────────────────────────────────────────
    verify_key = nacl.signing.VerifyKey(public_key_bytes)
    try:
        verify_key.verify(message, signature_bytes)
    except nacl.exceptions.BadSignatureError:
        return SignatureResult(
            valid=False,
            error="Ed25519 signature verification failed — request may be "
                  "tampered, replayed, or signed with a different key",
            error_code="VERIFICATION_FAILED",
        )

    return SignatureResult(valid=True)


def generate_test_keypair() -> tuple[str, str, str]:
    """Generate a fresh Ed25519 key pair for testing.

    Convenience function that returns the private key hex, public key
    hex, and a readily-usable verify key object.  This exists purely
    so that test code can produce known key pairs without importing
    nacl internals.

    Returns:
        A 3-tuple of ``(private_key_hex, public_key_hex, verify_key_hex)``.

        Note: ``public_key_hex`` and ``verify_key_hex`` are identical
        (the public half of the key pair).  Both are provided for
        caller convenience.
    """
    sk = nacl.signing.SigningKey.generate()
    vk = sk.verify_key
    sk_hex = sk.encode(nacl.encoding.HexEncoder).decode()
    vk_hex = vk.encode(nacl.encoding.HexEncoder).decode()
    return sk_hex, vk_hex, vk_hex


def sign_request_body(
    raw_body: bytes,
    timestamp: str,
    private_key_hex: str,
) -> str:
    """Sign a request body + timestamp with an Ed25519 private key.

    Constructs the signed message as ``timestamp (UTF-8) + raw_body``,
    matching Discord's signing scheme exactly.

    Args:
        raw_body: Raw HTTP request body bytes.
        timestamp: ASCII decimal timestamp string (e.g. ``"1701234567"``).
        private_key_hex: Hex-encoded 64-byte Ed25519 private (seed) key.

    Returns:
        Hex-encoded 64-byte Ed25519 signature.

    Raises:
        ValueError: If ``private_key_hex`` is not valid hex or has
            wrong length.
    """
    sk = nacl.signing.SigningKey(
        private_key_hex,
        encoder=nacl.encoding.HexEncoder,
    )
    message = timestamp.encode("utf-8") + raw_body
    return sk.sign(message).signature.hex()


# ── Exports ──────────────────────────────────────────────────────────────

__all__ = [
    # Constants
    "PUBLIC_KEY_BYTE_LENGTH",
    "SIGNATURE_BYTE_LENGTH",
    # Result type
    "SignatureResult",
    # Public API
    "verify_request_signature",
    # Test helpers
    "generate_test_keypair",
    "sign_request_body",
]
