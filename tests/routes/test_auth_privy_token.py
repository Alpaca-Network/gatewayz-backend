"""
Route-level tests for server-side Privy access-token verification on
POST /auth (gatewayz-backend#2248).

These tests exercise the FastAPI app through TestClient so the enforcement
helper (`src.routes.auth._enforce_privy_token`) is proven wired into the real
request path, not just callable in isolation (see
tests/security/test_privy_token.py for the verifier's own unit tests).
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from src.main import app
from src.routes.auth import PrivyAuthResponse

client = TestClient(app)

TEST_APP_ID = "test-app-id"
TEST_DID = "did:privy:cliduser123"


def _key_pair():
    private_key = ec.generate_private_key(ec.SECP256R1())
    return private_key, private_key.public_key()


def _pem(key) -> str:
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


def _mint_token(private_key, *, sub=TEST_DID, exp_delta=300):
    now = datetime.now(UTC)
    payload = {
        "sub": sub,
        "sid": "session-1",
        "aud": TEST_APP_ID,
        "iss": "privy.io",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=exp_delta)).timestamp()),
    }
    return jwt.encode(payload, private_key, algorithm="ES256")


def _auth_body(*, token=None):
    body = {
        "user": {
            "id": TEST_DID,
            "created_at": 1700000000,
            "linked_accounts": [],
            "mfa_methods": [],
            "has_accepted_terms": True,
            "is_guest": False,
        },
        "email": "wallet-user@example.com",
    }
    if token is not None:
        body["token"] = token
    return body


@pytest.fixture
def key_pair():
    return _key_pair()


@pytest.fixture(autouse=True)
def allow_rate_limit():
    """Rate limiting is exercised by its own tests; keep it out of the way here."""
    from src.services.auth_rate_limiting import AuthRateLimitResult

    with patch(
        "src.routes.auth.check_auth_rate_limit",
        return_value=AuthRateLimitResult(allowed=True, remaining=9, retry_after=None),
    ):
        yield


def _configure(monkeypatch, key_pair, *, mode):
    private_key, public_key = key_pair
    monkeypatch.setattr("src.security.privy_token.Config.PRIVY_APP_ID", TEST_APP_ID)
    monkeypatch.setattr("src.security.privy_token.Config.PRIVY_VERIFICATION_KEY", _pem(public_key))
    monkeypatch.setattr("src.security.privy_token.Config.PRIVY_TOKEN_VERIFICATION", mode)
    return private_key


class TestEnforceMode:
    def test_missing_token_returns_401(self, monkeypatch, key_pair):
        _configure(monkeypatch, key_pair, mode="enforce")

        response = client.post("/auth", json=_auth_body())

        assert response.status_code == 401
        assert response.json()["error"] == "privy_token_required"

    def test_sub_mismatch_returns_401(self, monkeypatch, key_pair):
        private_key = _configure(monkeypatch, key_pair, mode="enforce")
        token = _mint_token(private_key, sub="did:privy:someone-else")

        response = client.post("/auth", json=_auth_body(token=token))

        assert response.status_code == 401
        assert response.json()["error"] == "invalid_privy_token"
        assert response.json()["reason"] == "sub_mismatch"

    def test_expired_token_returns_401(self, monkeypatch, key_pair):
        private_key = _configure(monkeypatch, key_pair, mode="enforce")
        token = _mint_token(private_key, exp_delta=-3600)

        response = client.post("/auth", json=_auth_body(token=token))

        assert response.status_code == 401
        assert response.json()["reason"] == "expired"

    @patch("src.routes.auth._handle_existing_user")
    @patch("src.routes.auth.get_cached_user_by_privy_id")
    def test_valid_token_proceeds_past_the_gate(
        self, mock_get_cached_user, mock_handle_existing_user, monkeypatch, key_pair
    ):
        private_key = _configure(monkeypatch, key_pair, mode="enforce")
        token = _mint_token(private_key)
        mock_get_cached_user.return_value = {"id": 99, "privy_user_id": TEST_DID}
        mock_handle_existing_user.return_value = PrivyAuthResponse(
            success=True, message="Login successful", user_id=99
        )

        response = client.post("/auth", json=_auth_body(token=token))

        assert response.status_code == 200
        assert response.json()["user_id"] == 99
        mock_handle_existing_user.assert_called_once()


class TestLogMode:
    @patch("src.routes.auth._handle_existing_user")
    @patch("src.routes.auth.get_cached_user_by_privy_id")
    def test_missing_token_proceeds_and_logs_warning(
        self, mock_get_cached_user, mock_handle_existing_user, monkeypatch, key_pair, caplog
    ):
        _configure(monkeypatch, key_pair, mode="log")
        mock_get_cached_user.return_value = {"id": 99, "privy_user_id": TEST_DID}
        mock_handle_existing_user.return_value = PrivyAuthResponse(
            success=True, message="Login successful", user_id=99
        )

        with caplog.at_level("WARNING", logger="src.routes.auth"):
            response = client.post("/auth", json=_auth_body())

        assert response.status_code == 200
        assert "privy_token_unverified" in caplog.text
        assert "reason=missing" in caplog.text


class TestOffMode:
    @patch("src.routes.auth._handle_existing_user")
    @patch("src.routes.auth.get_cached_user_by_privy_id")
    def test_missing_token_is_untouched(
        self, mock_get_cached_user, mock_handle_existing_user, monkeypatch, key_pair
    ):
        _configure(monkeypatch, key_pair, mode="off")
        mock_get_cached_user.return_value = {"id": 99, "privy_user_id": TEST_DID}
        mock_handle_existing_user.return_value = PrivyAuthResponse(
            success=True, message="Login successful", user_id=99
        )

        response = client.post("/auth", json=_auth_body())

        assert response.status_code == 200
