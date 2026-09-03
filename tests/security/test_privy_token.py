"""Tests for src.security.privy_token (gatewayz-backend#2248)."""

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from src.security.privy_token import (
    PrivyTokenError,
    privy_verification_mode,
    verify_privy_access_token,
)

TEST_APP_ID = "test-app-id"
TEST_DID = "did:privy:abc123"


def _key_pair():
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_key = private_key.public_key()
    return private_key, public_key


def _pem(key) -> str:
    from cryptography.hazmat.primitives import serialization

    if isinstance(key, ec.EllipticCurvePrivateKey):
        return key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode()
    return key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()


def _mint_token(private_key, *, sub=TEST_DID, aud=TEST_APP_ID, iss="privy.io", exp_delta=300):
    now = datetime.now(UTC)
    payload = {
        "sub": sub,
        "sid": "session-1",
        "aud": aud,
        "iss": iss,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=exp_delta)).timestamp()),
    }
    return jwt.encode(payload, private_key, algorithm="ES256")


@pytest.fixture
def key_pair():
    return _key_pair()


@pytest.fixture(autouse=True)
def configured(monkeypatch, key_pair):
    private_key, public_key = key_pair
    monkeypatch.setattr("src.security.privy_token.Config.PRIVY_APP_ID", TEST_APP_ID)
    monkeypatch.setattr("src.security.privy_token.Config.PRIVY_VERIFICATION_KEY", _pem(public_key))
    monkeypatch.setattr("src.security.privy_token.Config.PRIVY_TOKEN_VERIFICATION", "enforce")
    return private_key, public_key


def test_valid_token_returns_claims(configured):
    private_key, _ = configured
    token = _mint_token(private_key)

    claims = verify_privy_access_token(token, expected_sub=TEST_DID)

    assert claims.sub == TEST_DID
    assert claims.sid == "session-1"
    assert claims.exp > 0


def test_wrong_sub_raises_sub_mismatch(configured):
    private_key, _ = configured
    token = _mint_token(private_key, sub="did:privy:someone-else")

    with pytest.raises(PrivyTokenError) as exc_info:
        verify_privy_access_token(token, expected_sub=TEST_DID)

    assert exc_info.value.reason == "sub_mismatch"


def test_expired_token_raises_expired(configured):
    private_key, _ = configured
    token = _mint_token(private_key, exp_delta=-3600)

    with pytest.raises(PrivyTokenError) as exc_info:
        verify_privy_access_token(token, expected_sub=TEST_DID)

    assert exc_info.value.reason == "expired"


def test_wrong_signing_key_raises_bad_signature(configured):
    other_private_key, _ = _key_pair()
    token = _mint_token(other_private_key)

    with pytest.raises(PrivyTokenError) as exc_info:
        verify_privy_access_token(token, expected_sub=TEST_DID)

    assert exc_info.value.reason == "bad_signature"


def test_wrong_audience_raises_bad_signature(configured):
    private_key, _ = configured
    token = _mint_token(private_key, aud="some-other-app")

    with pytest.raises(PrivyTokenError) as exc_info:
        verify_privy_access_token(token, expected_sub=TEST_DID)

    assert exc_info.value.reason == "bad_signature"


def test_wrong_issuer_raises_bad_signature(configured):
    private_key, _ = configured
    token = _mint_token(private_key, iss="not-privy.io")

    with pytest.raises(PrivyTokenError) as exc_info:
        verify_privy_access_token(token, expected_sub=TEST_DID)

    assert exc_info.value.reason == "bad_signature"


def test_garbage_token_raises_malformed(configured):
    with pytest.raises(PrivyTokenError) as exc_info:
        verify_privy_access_token("not-a-jwt", expected_sub=TEST_DID)

    assert exc_info.value.reason == "malformed"


def test_none_token_raises_missing(configured):
    with pytest.raises(PrivyTokenError) as exc_info:
        verify_privy_access_token(None, expected_sub=TEST_DID)

    assert exc_info.value.reason == "missing"


def test_missing_key_raises_not_configured(configured, monkeypatch):
    monkeypatch.setattr("src.security.privy_token.Config.PRIVY_VERIFICATION_KEY", None)
    private_key, _ = configured
    token = _mint_token(private_key)

    with pytest.raises(PrivyTokenError) as exc_info:
        verify_privy_access_token(token, expected_sub=TEST_DID)

    assert exc_info.value.reason == "not_configured"


def test_pem_with_literal_newline_escapes_is_normalized(configured, monkeypatch):
    private_key, public_key = configured
    escaped_pem = _pem(public_key).replace("\n", "\\n")
    monkeypatch.setattr("src.security.privy_token.Config.PRIVY_VERIFICATION_KEY", escaped_pem)
    token = _mint_token(private_key)

    claims = verify_privy_access_token(token, expected_sub=TEST_DID)

    assert claims.sub == TEST_DID


class TestPrivyVerificationMode:
    def test_explicit_mode_wins(self, monkeypatch):
        monkeypatch.setattr("src.security.privy_token.Config.PRIVY_TOKEN_VERIFICATION", "off")
        monkeypatch.setattr("src.security.privy_token.Config.PRIVY_VERIFICATION_KEY", "some-key")
        assert privy_verification_mode() == "off"

    def test_defaults_to_enforce_when_key_present(self, monkeypatch):
        monkeypatch.setattr("src.security.privy_token.Config.PRIVY_TOKEN_VERIFICATION", "")
        monkeypatch.setattr("src.security.privy_token.Config.PRIVY_VERIFICATION_KEY", "some-key")
        assert privy_verification_mode() == "enforce"

    def test_defaults_to_log_when_key_absent(self, monkeypatch):
        monkeypatch.setattr("src.security.privy_token.Config.PRIVY_TOKEN_VERIFICATION", "")
        monkeypatch.setattr("src.security.privy_token.Config.PRIVY_VERIFICATION_KEY", None)
        assert privy_verification_mode() == "log"
