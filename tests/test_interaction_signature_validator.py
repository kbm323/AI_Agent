"""Tests for Interaction Signature Validator (Sub-AC 1.1.2).

Covers Ed25519 signature verification on incoming Discord interaction
requests using the application's public key.  All tests use known key
pairs generated via PyNaCl — no Discord application registration,
network, or external service required.

Test categories
---------------
1. Happy path — valid signatures with known key pairs
2. Tampered body — different body bytes than what was signed
3. Tampered timestamp — different timestamp than what was signed
4. Tampered signature — flipped hex characters
5. Wrong public key — signature verified against a different key
6. Empty / missing parameters — signature, timestamp, public key, body
7. Invalid hex inputs — non-hex characters in key and signature
8. Wrong-length inputs — key or signature with incorrect byte count
9. Replay detection awareness — identical body with different timestamp
10. Edge cases — empty body, large body, unicode body, zero-length strings
11. Result type — SignatureResult .valid / .error / .error_code
12. Test helper functions — generate_test_keypair, sign_request_body
"""

from __future__ import annotations

import pytest

from src.interaction_signature_validator import (
    PUBLIC_KEY_BYTE_LENGTH,
    SIGNATURE_BYTE_LENGTH,
    SignatureResult,
    generate_test_keypair,
    sign_request_body,
    verify_request_signature,
)

# ═══════════════════════════════════════════════════════════════════════════
# Known key pair fixtures
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def known_keys() -> tuple[str, str]:
    """A fresh Ed25519 key pair: (private_key_hex, public_key_hex)."""
    private, public, _ = generate_test_keypair()
    return private, public


@pytest.fixture
def public_key(known_keys: tuple[str, str]) -> str:
    """The public key hex string."""
    return known_keys[1]


@pytest.fixture
def private_key(known_keys: tuple[str, str]) -> str:
    """The private key hex string (for signing)."""
    return known_keys[0]


@pytest.fixture
def sample_body() -> bytes:
    """A realistic Discord interaction JSON body."""
    return (
        b'{"id":"interaction_001","token":"tok_abc123","type":2,'
        b'"version":1,"channel_id":"channel_456",'
        b'"data":{"id":"cmd_001","name":"meeting","type":1,'
        b'"options":[{"name":"agenda","type":3,"value":"Design Review"}]}}'
    )


@pytest.fixture
def sample_timestamp() -> str:
    """A realistic Unix timestamp as a decimal string."""
    return "1701234567"


# ═══════════════════════════════════════════════════════════════════════════
# 1. Happy path — valid signatures
# ═══════════════════════════════════════════════════════════════════════════


class TestValidSignatures:
    """Cryptographically valid signatures must pass verification."""

    def test_valid_minimal_ping_body(
        self, private_key: str, public_key: str
    ) -> None:
        """A validly-signed minimal PING payload should verify."""
        body = b'{"type":1}'
        ts = "1234567890"
        sig = sign_request_body(body, ts, private_key)

        result = verify_request_signature(body, sig, ts, public_key)
        assert result.valid is True
        assert result.error == ""
        assert result.error_code == ""

    def test_valid_full_slash_command_body(
        self,
        private_key: str,
        public_key: str,
        sample_body: bytes,
        sample_timestamp: str,
    ) -> None:
        """A full APPLICATION_COMMAND payload should verify."""
        sig = sign_request_body(sample_body, sample_timestamp, private_key)

        result = verify_request_signature(
            sample_body, sig, sample_timestamp, public_key
        )
        assert result.valid is True

    def test_valid_with_unicode_payload(
        self, private_key: str, public_key: str
    ) -> None:
        """UTF-8 payloads containing Korean text should verify."""
        body = '{"agenda":"뮤직비디오 오프닝 아이디어"}'.encode()
        ts = "1701234567"
        sig = sign_request_body(body, ts, private_key)

        result = verify_request_signature(body, sig, ts, public_key)
        assert result.valid is True

    def test_multiple_valid_signatures_with_same_key(
        self, private_key: str, public_key: str
    ) -> None:
        """Multiple different bodies signed with the same key should all verify."""
        for i, body in enumerate([b'{"type":1}', b'{"type":2}', b'{"n":99}']):
            ts = str(1701234500 + i)
            sig = sign_request_body(body, ts, private_key)
            result = verify_request_signature(body, sig, ts, public_key)
            assert result.valid is True, f"Body {i} failed"


# ═══════════════════════════════════════════════════════════════════════════
# 2. Tampered body
# ═══════════════════════════════════════════════════════════════════════════


class TestTamperedBody:
    """Any change to the body bytes must cause verification failure."""

    def test_different_body_fails(
        self,
        private_key: str,
        public_key: str,
        sample_timestamp: str,
    ) -> None:
        """Sign body A, then present body B for verification."""
        original = b'{"type":1}'
        sig = sign_request_body(original, sample_timestamp, private_key)

        result = verify_request_signature(
            b'{"type":2}', sig, sample_timestamp, public_key
        )
        assert result.valid is False
        assert result.error_code == "VERIFICATION_FAILED"

    def test_extra_byte_fails(
        self,
        private_key: str,
        public_key: str,
        sample_timestamp: str,
    ) -> None:
        """Appending one byte to the body should break the signature."""
        original = b'{"type":1}'
        sig = sign_request_body(original, sample_timestamp, private_key)

        result = verify_request_signature(
            original + b" ",
            sig,
            sample_timestamp,
            public_key,
        )
        assert result.valid is False

    def test_truncated_body_fails(
        self,
        private_key: str,
        public_key: str,
        sample_timestamp: str,
    ) -> None:
        """Removing one byte from the body should break the signature."""
        original = b'{"type":1}'
        sig = sign_request_body(original, sample_timestamp, private_key)

        result = verify_request_signature(
            original[:-1], sig, sample_timestamp, public_key
        )
        assert result.valid is False

    def test_empty_body_with_signature_for_nonempty_fails(
        self,
        private_key: str,
        public_key: str,
        sample_timestamp: str,
    ) -> None:
        """Signing a non-empty body, then presenting empty body."""
        original = b'{"type":1}'
        sig = sign_request_body(original, sample_timestamp, private_key)

        result = verify_request_signature(
            b"", sig, sample_timestamp, public_key
        )
        # Empty body should be rejected before crypto (EMPTY_BODY),
        # or if the body check passes the crypto will fail.
        assert result.valid is False
        assert result.error_code in ("VERIFICATION_FAILED", "EMPTY_BODY")


# ═══════════════════════════════════════════════════════════════════════════
# 3. Tampered timestamp
# ═══════════════════════════════════════════════════════════════════════════


class TestTamperedTimestamp:
    """A different timestamp than what was signed must cause failure."""

    def test_wrong_timestamp_fails(
        self,
        private_key: str,
        public_key: str,
        sample_body: bytes,
        sample_timestamp: str,
    ) -> None:
        sig = sign_request_body(sample_body, sample_timestamp, private_key)

        result = verify_request_signature(
            sample_body, sig, "9999999999", public_key
        )
        assert result.valid is False
        assert result.error_code == "VERIFICATION_FAILED"

    def test_replay_with_same_body_different_timestamp(
        self,
        private_key: str,
        public_key: str,
        sample_body: bytes,
    ) -> None:
        """A valid signature from one timestamp is not valid for another."""
        sig = sign_request_body(sample_body, "1000000000", private_key)

        result = verify_request_signature(
            sample_body, sig, "2000000000", public_key
        )
        assert result.valid is False

    def test_timestamp_one_second_off_fails(
        self,
        private_key: str,
        public_key: str,
        sample_body: bytes,
    ) -> None:
        """Even a 1-second difference in timestamp breaks the signature."""
        sig = sign_request_body(sample_body, "1701234567", private_key)

        result = verify_request_signature(
            sample_body, sig, "1701234568", public_key
        )
        assert result.valid is False


# ═══════════════════════════════════════════════════════════════════════════
# 4. Tampered signature
# ═══════════════════════════════════════════════════════════════════════════


class TestTamperedSignature:
    """Any modification to the hex signature must cause failure."""

    def test_flipped_first_hex_char_fails(
        self,
        private_key: str,
        public_key: str,
        sample_body: bytes,
        sample_timestamp: str,
    ) -> None:
        sig = sign_request_body(sample_body, sample_timestamp, private_key)
        tampered = (
            "f" + sig[1:] if sig[0] != "f" else "0" + sig[1:]
        )

        result = verify_request_signature(
            sample_body, tampered, sample_timestamp, public_key
        )
        assert result.valid is False
        assert result.error_code == "VERIFICATION_FAILED"

    def test_flipped_last_hex_char_fails(
        self,
        private_key: str,
        public_key: str,
        sample_body: bytes,
        sample_timestamp: str,
    ) -> None:
        sig = sign_request_body(sample_body, sample_timestamp, private_key)
        tampered = sig[:-1] + ("f" if sig[-1] != "f" else "0")

        result = verify_request_signature(
            sample_body, tampered, sample_timestamp, public_key
        )
        assert result.valid is False

    def test_tampered_middle_byte_fails(
        self,
        private_key: str,
        public_key: str,
        sample_body: bytes,
        sample_timestamp: str,
    ) -> None:
        sig = sign_request_body(sample_body, sample_timestamp, private_key)
        # Flip bytes in the middle of the signature
        mid = len(sig) // 2
        # Flip two hex chars (one byte) at midpoint
        flipped_byte = f"{15 - int(sig[mid], 16):x}{15 - int(sig[mid + 1], 16):x}"
        tampered = sig[:mid] + flipped_byte + sig[mid + 2:]

        result = verify_request_signature(
            sample_body, tampered, sample_timestamp, public_key
        )
        assert result.valid is False


# ═══════════════════════════════════════════════════════════════════════════
# 5. Wrong public key
# ═══════════════════════════════════════════════════════════════════════════


class TestWrongPublicKey:
    """Signature verified against a different key must fail."""

    def test_different_key_pair_fails(
        self,
        private_key: str,
        sample_body: bytes,
        sample_timestamp: str,
    ) -> None:
        """Sign with key A, verify with key B."""
        sig = sign_request_body(sample_body, sample_timestamp, private_key)

        # Generate an entirely different key pair
        _, wrong_public, _ = generate_test_keypair()

        result = verify_request_signature(
            sample_body, sig, sample_timestamp, wrong_public
        )
        assert result.valid is False
        assert result.error_code == "VERIFICATION_FAILED"

    def test_distinct_keys_produce_distinct_public_keys(self) -> None:
        """Ensure test_keypair() actually produces different keys."""
        pk1 = generate_test_keypair()[1]
        pk2 = generate_test_keypair()[1]
        assert pk1 != pk2, "generate_test_keypair should produce unique keys"


# ═══════════════════════════════════════════════════════════════════════════
# 6. Empty / missing parameters
# ═══════════════════════════════════════════════════════════════════════════


class TestMissingParameters:
    """Empty or whitespace-only parameters produce MISSING_PARAMETER."""

    def test_empty_signature(
        self, public_key: str, sample_body: bytes, sample_timestamp: str
    ) -> None:
        result = verify_request_signature(
            sample_body, "", sample_timestamp, public_key
        )
        assert result.valid is False
        assert result.error_code == "MISSING_PARAMETER"
        assert "signature_hex" in result.error

    def test_empty_timestamp(
        self, public_key: str, sample_body: bytes
    ) -> None:
        result = verify_request_signature(
            sample_body, "aa" * 64, "", public_key
        )
        assert result.valid is False
        assert result.error_code == "MISSING_PARAMETER"
        assert "timestamp" in result.error

    def test_empty_public_key(
        self, sample_body: bytes, sample_timestamp: str
    ) -> None:
        result = verify_request_signature(
            sample_body, "aa" * 64, sample_timestamp, ""
        )
        assert result.valid is False
        assert result.error_code == "MISSING_PARAMETER"

    def test_all_three_empty(
        self, sample_body: bytes
    ) -> None:
        result = verify_request_signature(sample_body, "", "", "")
        assert result.valid is False
        assert result.error_code == "MISSING_PARAMETER"
        for name in ("signature_hex", "timestamp", "public_key_hex"):
            assert name in result.error

    def test_whitespace_only_parameters(
        self, sample_body: bytes
    ) -> None:
        """Whitespace-only strings should be treated as empty."""
        result = verify_request_signature(
            sample_body, "   ", "   ", "   "
        )
        assert result.valid is False
        assert result.error_code == "MISSING_PARAMETER"


# ═══════════════════════════════════════════════════════════════════════════
# 7. Invalid hex inputs
# ═══════════════════════════════════════════════════════════════════════════


class TestInvalidHexInputs:
    """Non-hex characters in key or signature produce structured errors."""

    def test_non_hex_public_key(
        self, sample_body: bytes, sample_timestamp: str
    ) -> None:
        result = verify_request_signature(
            sample_body, "aa" * 64, sample_timestamp, "zz" * 32
        )
        assert result.valid is False
        assert result.error_code == "INVALID_KEY_HEX"

    def test_non_hex_signature(
        self, public_key: str, sample_body: bytes, sample_timestamp: str
    ) -> None:
        result = verify_request_signature(
            sample_body, "zz" * 64, sample_timestamp, public_key
        )
        assert result.valid is False
        assert result.error_code == "INVALID_SIGNATURE_HEX"

    def test_mixed_invalid_hex_in_public_key(
        self, sample_body: bytes, sample_timestamp: str
    ) -> None:
        result = verify_request_signature(
            sample_body,
            "aa" * 64,
            sample_timestamp,
            "gg" + "aa" * 31,  # 'gg' is not valid hex
        )
        assert result.valid is False
        assert result.error_code == "INVALID_KEY_HEX"

    def test_odd_length_hex_signature(
        self, public_key: str, sample_body: bytes, sample_timestamp: str
    ) -> None:
        """An odd-length hex string cannot be decoded by bytes.fromhex."""
        result = verify_request_signature(
            sample_body, "a" * 127, sample_timestamp, public_key
        )
        assert result.valid is False
        # bytes.fromhex rejects odd-length strings
        assert result.error_code == "INVALID_SIGNATURE_HEX"


# ═══════════════════════════════════════════════════════════════════════════
# 8. Wrong-length inputs
# ═══════════════════════════════════════════════════════════════════════════


class TestWrongLengthInputs:
    """Key or signature with correct hex but wrong byte count."""

    def test_too_short_public_key(
        self, sample_body: bytes, sample_timestamp: str
    ) -> None:
        result = verify_request_signature(
            sample_body,
            "aa" * 64,
            sample_timestamp,
            "aa" * 10,  # only 10 bytes, need 32
        )
        assert result.valid is False
        assert result.error_code == "BAD_KEY_LENGTH"
        assert str(PUBLIC_KEY_BYTE_LENGTH) in result.error

    def test_too_long_public_key(
        self, sample_body: bytes, sample_timestamp: str
    ) -> None:
        result = verify_request_signature(
            sample_body,
            "aa" * 64,
            sample_timestamp,
            "aa" * 64,  # 64 bytes, need 32
        )
        assert result.valid is False
        assert result.error_code == "BAD_KEY_LENGTH"

    def test_too_short_signature(
        self, public_key: str, sample_body: bytes, sample_timestamp: str
    ) -> None:
        result = verify_request_signature(
            sample_body,
            "aa" * 10,  # only 10 bytes, need 64
            sample_timestamp,
            public_key,
        )
        assert result.valid is False
        assert result.error_code == "BAD_SIGNATURE_LENGTH"
        assert str(SIGNATURE_BYTE_LENGTH) in result.error

    def test_too_long_signature(
        self, public_key: str, sample_body: bytes, sample_timestamp: str
    ) -> None:
        result = verify_request_signature(
            sample_body,
            "aa" * 128,  # 128 bytes, need 64
            sample_timestamp,
            public_key,
        )
        assert result.valid is False
        assert result.error_code == "BAD_SIGNATURE_LENGTH"

    def test_zero_length_hex_public_key(
        self, sample_body: bytes, sample_timestamp: str
    ) -> None:
        """Empty hex string for public key is caught early as missing."""
        result = verify_request_signature(
            sample_body, "aa" * 64, sample_timestamp, ""
        )
        assert result.valid is False
        # Empty string caught by MISSING_PARAMETER before length check
        assert result.error_code == "MISSING_PARAMETER"


# ═══════════════════════════════════════════════════════════════════════════
# 9. Replay detection awareness
# ═══════════════════════════════════════════════════════════════════════════


class TestReplayDetectionAwareness:
    """While the module doesn't implement replay protection (that's a
    higher-level concern), the Ed25519 scheme inherently binds the
    signature to both body AND timestamp, so reusing a signature with
    a different body or timestamp will always fail.

    These tests verify that property.
    """

    def test_signature_not_valid_for_different_timestamp(
        self,
        private_key: str,
        public_key: str,
        sample_body: bytes,
    ) -> None:
        """A signature is bound to its specific timestamp."""
        ts1 = "1701234567"
        ts2 = "1701234568"
        sig = sign_request_body(sample_body, ts1, private_key)

        # Same signature with different timestamp → fail
        result = verify_request_signature(sample_body, sig, ts2, public_key)
        assert result.valid is False

    def test_signature_not_valid_for_different_body_same_timestamp(
        self,
        private_key: str,
        public_key: str,
    ) -> None:
        """Same timestamp, different body → verification fails."""
        ts = "1701234567"
        sig = sign_request_body(b'{"type":1}', ts, private_key)

        result = verify_request_signature(b'{"type":2}', sig, ts, public_key)
        assert result.valid is False

    def test_same_body_same_timestamp_different_key_fails(
        self,
        private_key: str,
        sample_body: bytes,
    ) -> None:
        """Replay across different applications (different keys) fails."""
        ts = "1701234567"
        sig = sign_request_body(sample_body, ts, private_key)
        _, other_public, _ = generate_test_keypair()

        result = verify_request_signature(sample_body, sig, ts, other_public)
        assert result.valid is False


# ═══════════════════════════════════════════════════════════════════════════
# 10. Edge cases
# ═══════════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    """Unusual but valid inputs and boundary conditions."""

    def test_empty_body_rejected(
        self, public_key: str, sample_timestamp: str
    ) -> None:
        """Empty body bytes should be rejected early."""
        result = verify_request_signature(
            b"", "aa" * 64, sample_timestamp, public_key
        )
        assert result.valid is False
        assert result.error_code == "EMPTY_BODY"

    def test_large_body_verifies(
        self, private_key: str, public_key: str
    ) -> None:
        """A 100 KB body should verify correctly (Ed25519 is O(n) on body)."""
        body = b'{"data":"' + b"x" * 100_000 + b'"}'
        ts = "1701234567"
        sig = sign_request_body(body, ts, private_key)

        result = verify_request_signature(body, sig, ts, public_key)
        assert result.valid is True

    def test_body_with_null_bytes(
        self, private_key: str, public_key: str
    ) -> None:
        """Bodies containing null bytes should verify."""
        body = b'{"bin":"' + b"\x00\x01\x02\x03" + b'"}'
        ts = "1701234567"
        sig = sign_request_body(body, ts, private_key)

        result = verify_request_signature(body, sig, ts, public_key)
        assert result.valid is True

    def test_body_with_non_ascii_bytes(
        self, private_key: str, public_key: str
    ) -> None:
        """Raw bytes > 127 should not break verification."""
        body = bytes(range(256))
        ts = "1701234567"
        sig = sign_request_body(body, ts, private_key)

        result = verify_request_signature(body, sig, ts, public_key)
        assert result.valid is True

    def test_timestamp_with_leading_zeros(
        self, private_key: str, public_key: str
    ) -> None:
        """Timestamp like '0000000001' is valid ASCII decimal."""
        body = b'{"type":1}'
        ts = "0000000001"
        sig = sign_request_body(body, ts, private_key)

        result = verify_request_signature(body, sig, ts, public_key)
        assert result.valid is True

    def test_very_long_timestamp_string(
        self, private_key: str, public_key: str
    ) -> None:
        """A very long timestamp string (100 digits) still works."""
        body = b'{"type":1}'
        ts = "1" * 100
        sig = sign_request_body(body, ts, private_key)

        result = verify_request_signature(body, sig, ts, public_key)
        assert result.valid is True

    def test_hex_characters_case_insensitive(
        self,
        private_key: str,
        public_key: str,
        sample_body: bytes,
        sample_timestamp: str,
    ) -> None:
        """Uppercase and lowercase hex are both accepted by bytes.fromhex."""
        # Sign with lowercase
        sig_lower = sign_request_body(sample_body, sample_timestamp, private_key)
        sig_upper = sig_lower.upper()

        # Uppercase signature should verify against lowercase public key
        result = verify_request_signature(
            sample_body, sig_upper, sample_timestamp, public_key
        )
        assert result.valid is True

        # Uppercase public key should work
        result = verify_request_signature(
            sample_body,
            sig_lower,
            sample_timestamp,
            public_key.upper(),
        )
        assert result.valid is True


# ═══════════════════════════════════════════════════════════════════════════
# 11. SignatureResult type
# ═══════════════════════════════════════════════════════════════════════════


class TestSignatureResultType:
    """The SignatureResult dataclass behaves correctly."""

    def test_valid_result_has_no_error_fields(self) -> None:
        result = SignatureResult(valid=True)
        assert result.valid is True
        assert result.error == ""
        assert result.error_code == ""

    def test_invalid_result_stores_error(self) -> None:
        result = SignatureResult(
            valid=False,
            error="Something went wrong",
            error_code="SOME_CODE",
        )
        assert result.valid is False
        assert result.error == "Something went wrong"
        assert result.error_code == "SOME_CODE"

    def test_result_is_frozen(self) -> None:
        """SignatureResult is a frozen dataclass — fields are immutable."""
        result = SignatureResult(valid=True)
        with pytest.raises((TypeError, AttributeError, ValueError)):
            result.valid = False  # type: ignore[misc]

    def test_result_repr(self) -> None:
        """The repr should include key fields for debugging."""
        result = SignatureResult(
            valid=False,
            error="bad",
            error_code="ERR",
        )
        r = repr(result)
        assert "valid=False" in r or "valid" in r
        assert "bad" in r
        assert "ERR" in r

    def test_equality(self) -> None:
        a = SignatureResult(valid=True)
        b = SignatureResult(valid=True)
        c = SignatureResult(valid=False, error="x", error_code="E")
        d = SignatureResult(valid=False, error="x", error_code="E")

        assert a == b
        assert c == d
        assert a != c


# ═══════════════════════════════════════════════════════════════════════════
# 12. Test helper functions
# ═══════════════════════════════════════════════════════════════════════════


class TestGenerateTestKeypair:
    """generate_test_keypair produces valid, usable keys."""

    def test_produces_valid_hex_strings(self) -> None:
        private, public, verify = generate_test_keypair()
        # All should be hex strings
        bytes.fromhex(private)
        bytes.fromhex(public)
        bytes.fromhex(verify)

    def test_private_key_is_32_bytes_seed(self) -> None:
        """Ed25519 SigningKey.generate() returns a 32-byte seed."""
        private, _, _ = generate_test_keypair()
        assert len(bytes.fromhex(private)) == 32

    def test_public_key_is_32_bytes(self) -> None:
        _, public, _ = generate_test_keypair()
        assert len(bytes.fromhex(public)) == PUBLIC_KEY_BYTE_LENGTH

    def test_public_and_verify_are_identical(self) -> None:
        _, public, verify = generate_test_keypair()
        assert public == verify

    def test_multiple_calls_produce_different_keys(self) -> None:
        p1, _, _ = generate_test_keypair()
        p2, _, _ = generate_test_keypair()
        assert p1 != p2

    def test_produced_keys_work(self) -> None:
        """The generated key pair should pass a full sign+verify round-trip."""
        private, public, _ = generate_test_keypair()
        body = b'{"test":true}'
        ts = "1701234567"
        sig = sign_request_body(body, ts, private)

        result = verify_request_signature(body, sig, ts, public)
        assert result.valid is True


class TestSignRequestBody:
    """sign_request_body produces correct signatures."""

    def test_produces_128_char_hex_string(
        self, private_key: str, sample_body: bytes
    ) -> None:
        sig = sign_request_body(sample_body, "123", private_key)
        assert len(sig) == 128  # 64 bytes * 2 hex chars
        # Must be valid hex
        bytes.fromhex(sig)

    def test_deterministic_for_same_inputs(
        self, private_key: str
    ) -> None:
        """Same body, timestamp, and key → same signature (deterministic Ed25519)."""
        body = b'{"type":1}'
        ts = "1234567890"
        sig1 = sign_request_body(body, ts, private_key)
        sig2 = sign_request_body(body, ts, private_key)
        assert sig1 == sig2

    def test_different_bodies_produce_different_signatures(
        self, private_key: str
    ) -> None:
        ts = "1234567890"
        sig1 = sign_request_body(b'{"type":1}', ts, private_key)
        sig2 = sign_request_body(b'{"type":2}', ts, private_key)
        assert sig1 != sig2

    def test_different_timestamps_produce_different_signatures(
        self, private_key: str
    ) -> None:
        body = b'{"type":1}'
        sig1 = sign_request_body(body, "100", private_key)
        sig2 = sign_request_body(body, "200", private_key)
        assert sig1 != sig2

    def test_different_keys_produce_different_signatures(
        self, sample_body: bytes
    ) -> None:
        ts = "1234567890"
        pk1, _, _ = generate_test_keypair()
        pk2, _, _ = generate_test_keypair()
        sig1 = sign_request_body(sample_body, ts, pk1)
        sig2 = sign_request_body(sample_body, ts, pk2)
        assert sig1 != sig2

    def test_raises_on_invalid_private_key_hex(self) -> None:
        with pytest.raises((TypeError, AttributeError, ValueError)):
            sign_request_body(b"{}", "123", "not-valid-hex")

    def test_raises_on_wrong_length_private_key(self) -> None:
        with pytest.raises((TypeError, AttributeError, ValueError)):
            sign_request_body(b"{}", "123", "aa" * 10)


# ═══════════════════════════════════════════════════════════════════════════
# 13. Integration — full Discord-like workflow
# ═══════════════════════════════════════════════════════════════════════════


class TestFullWorkflow:
    """End-to-end: sign like Discord, verify with known key."""

    def test_discord_documented_workflow(
        self, private_key: str, public_key: str
    ) -> None:
        """Simulate exactly what Discord does on their side."""
        # Step 1: Discord constructs the interaction payload
        payload = {
            "id": "interaction_abc",
            "token": "tok_xyz",
            "type": 2,
            "version": 1,
            "channel_id": "channel_789",
            "data": {
                "id": "cmd_data",
                "name": "meeting",
                "type": 1,
                "options": [
                    {"name": "agenda", "type": 3, "value": "Review"}
                ],
            },
        }
        import json

        raw_body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        timestamp = "1701234567"

        # Step 2: Discord signs the body with its private key
        signature = sign_request_body(raw_body, timestamp, private_key)

        # Step 3: Our server receives the request and verifies
        result = verify_request_signature(
            raw_body, signature, timestamp, public_key
        )

        assert result.valid is True

    def test_reject_invalid_signature_at_boundary(self) -> None:
        """A completely random signature string should be rejected."""
        _, public, _ = generate_test_keypair()

        result = verify_request_signature(
            b'{"type":2}',
            "aa" * 64,  # arbitrary signature
            "1701234567",
            public,
        )
        assert result.valid is False

    def test_round_trip_with_multiple_keys(self) -> None:
        """Generate 5 key pairs, each should work for its own signature."""
        for i in range(5):
            private, public, _ = generate_test_keypair()
            body = b'{"seq":' + str(i).encode() + b'}'
            ts = str(1701234500 + i)
            sig = sign_request_body(body, ts, private)

            result = verify_request_signature(body, sig, ts, public)
            assert result.valid is True

            # But fails with a different key
            _, other_public, _ = generate_test_keypair()
            result = verify_request_signature(body, sig, ts, other_public)
            assert result.valid is False
