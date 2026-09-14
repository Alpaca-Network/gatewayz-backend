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


def test_missing_key_and_app_id_raises_not_configured(configured, monkeypatch):
    # With no PEM the verifier falls back to JWKS (keyed by app id); with
    # neither there is nothing to verify against.
    monkeypatch.setattr("src.security.privy_token.Config.PRIVY_VERIFICATION_KEY", None)
    monkeypatch.setattr("src.security.privy_token.Config.PRIVY_APP_ID", None)
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

    def test_defaults_to_log_when_nothing_can_verify(self, monkeypatch):
        monkeypatch.setattr("src.security.privy_token.Config.PRIVY_TOKEN_VERIFICATION", "")
        monkeypatch.setattr("src.security.privy_token.Config.PRIVY_VERIFICATION_KEY", None)
        monkeypatch.setattr("src.security.privy_token.Config.PRIVY_APP_ID", None)
        assert privy_verification_mode() == "log"

    def test_explicit_log_still_honoured(self, monkeypatch):
        monkeypatch.setattr("src.security.privy_token.Config.PRIVY_TOKEN_VERIFICATION", "log")
        monkeypatch.setattr("src.security.privy_token.Config.PRIVY_APP_ID", "app-id")
        assert privy_verification_mode() == "log"


# ---------------------------------------------------------------------------
# JWKS mode (no PEM configured): keys fetched from Privy, selected by kid
# ---------------------------------------------------------------------------
import base64 as _b64

from cryptography.hazmat.primitives.asymmetric import ec as _ec

from src.security import privy_token as _pt


def _jwk_for(public_key, kid: str) -> dict:
    nums = public_key.public_numbers()

    def b64u(i: int) -> str:
        return _b64.urlsafe_b64encode(i.to_bytes(32, "big")).rstrip(b"=").decode()

    return {
        "kty": "EC",
        "crv": "P-256",
        "alg": "ES256",
        "use": "sig",
        "kid": kid,
        "x": b64u(nums.x),
        "y": b64u(nums.y),
    }


def _mint_with_kid(private_key, kid: str, **kw):
    now = datetime.now(UTC)
    payload = {
        "sub": kw.get("sub", TEST_DID),
        "sid": "session-1",
        "aud": TEST_APP_ID,
        "iss": "privy.io",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=300)).timestamp()),
    }
    return jwt.encode(payload, private_key, algorithm="ES256", headers={"kid": kid})


class _FakeResp:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")

    def json(self):
        return self._payload


@pytest.fixture
def jwks_mode(monkeypatch, key_pair):
    """Two rotated keys published via JWKS; no PEM configured."""
    _pt.clear_jwks_cache()
    monkeypatch.setattr("src.security.privy_token.Config.PRIVY_VERIFICATION_KEY", None)
    monkeypatch.setattr("src.security.privy_token.Config.PRIVY_JWKS_URL", None)
    priv_a, pub_a = key_pair
    priv_b = _ec.generate_private_key(_ec.SECP256R1())
    pub_b = priv_b.public_key()
    jwks = {"keys": [_jwk_for(pub_a, "kid-a"), _jwk_for(pub_b, "kid-b")]}
    calls = {"n": 0, "urls": []}

    def fake_get(url, timeout):
        calls["n"] += 1
        calls["urls"].append(url)
        return _FakeResp(jwks)

    monkeypatch.setattr("src.security.privy_token.httpx.get", fake_get)
    yield {"priv_a": priv_a, "priv_b": priv_b, "jwks": jwks, "calls": calls}
    _pt.clear_jwks_cache()


def test_jwks_verifies_tokens_from_either_rotated_key(jwks_mode):
    claims_a = verify_privy_access_token(_mint_with_kid(jwks_mode["priv_a"], "kid-a"), TEST_DID)
    claims_b = verify_privy_access_token(_mint_with_kid(jwks_mode["priv_b"], "kid-b"), TEST_DID)
    assert claims_a.sub == claims_b.sub == TEST_DID
    # Fetched once and cached for the second call.
    assert jwks_mode["calls"]["n"] == 1
    assert (
        jwks_mode["calls"]["urls"][0]
        == f"https://auth.privy.io/api/v1/apps/{TEST_APP_ID}/jwks.json"
    )


def test_jwks_unknown_kid_refreshes_once_then_rejects(jwks_mode):
    other = _ec.generate_private_key(_ec.SECP256R1())
    with pytest.raises(PrivyTokenError) as exc:
        verify_privy_access_token(_mint_with_kid(other, "kid-zzz"), TEST_DID)
    assert exc.value.reason == "bad_signature"
    assert jwks_mode["calls"]["n"] == 2  # initial + one forced refresh


def test_jwks_wrong_key_for_kid_is_bad_signature(jwks_mode):
    # Signed with key B but claims kid-a -> signature check must fail.
    with pytest.raises(PrivyTokenError) as exc:
        verify_privy_access_token(_mint_with_kid(jwks_mode["priv_b"], "kid-a"), TEST_DID)
    assert exc.value.reason == "bad_signature"


def test_jwks_fetch_failure_without_cache_fails_closed(monkeypatch, jwks_mode):
    def boom(url, timeout):
        raise RuntimeError("network down")

    monkeypatch.setattr("src.security.privy_token.httpx.get", boom)
    with pytest.raises(PrivyTokenError) as exc:
        verify_privy_access_token(_mint_with_kid(jwks_mode["priv_a"], "kid-a"), TEST_DID)
    assert exc.value.reason == "jwks_unavailable"


def test_jwks_fetch_failure_uses_stale_cache(monkeypatch, jwks_mode):
    verify_privy_access_token(_mint_with_kid(jwks_mode["priv_a"], "kid-a"), TEST_DID)  # warm cache
    monkeypatch.setattr("src.security.privy_token._JWKS_TTL_SECONDS", 0)  # force refresh path

    def boom(url, timeout):
        raise RuntimeError("network down")

    monkeypatch.setattr("src.security.privy_token.httpx.get", boom)
    claims = verify_privy_access_token(_mint_with_kid(jwks_mode["priv_a"], "kid-a"), TEST_DID)
    assert claims.sub == TEST_DID


def test_pem_env_overrides_jwks(monkeypatch, jwks_mode, key_pair):
    _, pub_a = key_pair
    monkeypatch.setattr("src.security.privy_token.Config.PRIVY_VERIFICATION_KEY", _pem(pub_a))
    verify_privy_access_token(_mint_with_kid(jwks_mode["priv_a"], "kid-a"), TEST_DID)
    assert jwks_mode["calls"]["n"] == 0


def test_jwks_mode_defaults_to_enforce(monkeypatch, jwks_mode):
    # Regression: JWKS-only prod defaulted to "log" and accepted forged tokens.
    monkeypatch.setattr("src.security.privy_token.Config.PRIVY_TOKEN_VERIFICATION", None)
    assert privy_verification_mode() == "enforce"
