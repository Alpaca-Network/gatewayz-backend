"""
Route-level tests for the Privy app-migration adoption wiring in POST /auth
(docs/PRIVY_MIGRATION.md).

These prove src/routes/auth.py calls
src.services.privy_migration.attempt_adoption only in the right place (a
token-verified DID with no existing row, PRIVY_MIGRATION_MODE=adopt), with
only the verified DID -- never the client-supplied request body. Adoption's
own matching/audit logic is covered by tests/services/test_privy_migration.py.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from src.main import app
from src.routes.auth import PrivyAuthResponse

client = TestClient(app)

TEST_APP_ID = "test-app-id"
TEST_DID = "did:privy:newappuser123"

_NEW_ACCOUNT_RESULT = {
    "user_id": 900,
    "username": "newappuser123",
    "credits": 0,
    "primary_api_key": "gw_test_new_900",
    "subscription_status": "inactive",
    "tier": "basic",
    "trial_expires_at": None,
    "subscription_end_date": None,
}


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


def _auth_body(*, token=None, email="attacker-controlled@example.com"):
    body = {
        "user": {
            "id": TEST_DID,
            "created_at": 1700000000,
            "linked_accounts": [],
            "mfa_methods": [],
            "has_accepted_terms": True,
            "is_guest": False,
        },
        "email": email,
    }
    if token is not None:
        body["token"] = token
    return body


@pytest.fixture
def key_pair():
    return _key_pair()


@pytest.fixture(autouse=True)
def allow_rate_limit():
    from src.services.auth_rate_limiting import AuthRateLimitResult

    with patch(
        "src.routes.auth.check_auth_rate_limit",
        return_value=AuthRateLimitResult(allowed=True, remaining=9, retry_after=None),
    ):
        yield


def _configure_token(monkeypatch, key_pair, *, mode="enforce"):
    private_key, public_key = key_pair
    monkeypatch.setattr("src.security.privy_token.Config.PRIVY_APP_ID", TEST_APP_ID)
    monkeypatch.setattr("src.security.privy_token.Config.PRIVY_VERIFICATION_KEY", _pem(public_key))
    monkeypatch.setattr("src.security.privy_token.Config.PRIVY_TOKEN_VERIFICATION", mode)
    return private_key


class TestAdoptionCalledOnVerifiedUnknownDid:
    @patch("src.routes.auth._handle_existing_user")
    @patch("src.routes.auth.attempt_adoption", new_callable=AsyncMock)
    @patch("src.routes.auth.migration_mode_is_adopt", return_value=True)
    @patch("src.routes.auth.users_module.get_user_by_privy_id", return_value=None)
    @patch("src.routes.auth.get_cached_user_by_privy_id", return_value=None)
    def test_adoption_success_short_circuits_to_existing_user(
        self,
        mock_cached_privy,
        mock_get_by_privy_id,
        mock_mode,
        mock_attempt_adoption,
        mock_handle_existing_user,
        monkeypatch,
        key_pair,
    ):
        private_key = _configure_token(monkeypatch, key_pair)
        token = _mint_token(private_key)
        adopted_user = {"id": 77, "privy_user_id": TEST_DID, "privy_app_id": "new-app"}
        mock_attempt_adoption.return_value = adopted_user
        mock_handle_existing_user.return_value = PrivyAuthResponse(
            success=True, message="Login successful", user_id=77
        )

        response = client.post("/auth", json=_auth_body(token=token))

        assert response.status_code == 200
        assert response.json()["user_id"] == 77

        mock_attempt_adoption.assert_called_once()
        call_kwargs = mock_attempt_adoption.call_args.kwargs
        assert call_kwargs["new_did"] == TEST_DID
        assert call_kwargs["token_verified"] is True
        # The client-supplied body (in particular its `email`) must never
        # reach adoption -- only the verified DID does.
        assert "email" not in call_kwargs
        assert "request" not in call_kwargs or not isinstance(call_kwargs.get("request"), dict)

        mock_handle_existing_user.assert_called_once()
        assert mock_handle_existing_user.call_args.kwargs["existing_user"] == adopted_user

    @patch("src.routes.auth.users_module.create_enhanced_user")
    @patch("src.routes.auth._generate_unique_username")
    @patch("src.routes.auth.supabase_config.get_supabase_client")
    @patch("src.routes.auth.users_module.get_user_by_email", return_value=None)
    @patch("src.routes.auth.attempt_adoption", new_callable=AsyncMock)
    @patch("src.routes.auth.migration_mode_is_adopt", return_value=True)
    @patch("src.routes.auth.users_module.get_user_by_privy_id", return_value=None)
    @patch("src.routes.auth.get_cached_user_by_privy_id", return_value=None)
    def test_no_adoption_match_falls_through_to_new_account(
        self,
        mock_cached_privy,
        mock_get_by_privy_id,
        mock_mode,
        mock_attempt_adoption,
        mock_get_by_email,
        mock_get_client,
        mock_gen_username,
        mock_create_user,
        monkeypatch,
        key_pair,
    ):
        private_key = _configure_token(monkeypatch, key_pair)
        token = _mint_token(private_key)
        mock_attempt_adoption.return_value = None
        mock_gen_username.return_value = "newappuser123"
        mock_create_user.return_value = _NEW_ACCOUNT_RESULT

        response = client.post("/auth", json=_auth_body(token=token))

        assert response.status_code == 200
        assert response.json()["is_new_user"] is True
        assert response.json()["user_id"] == 900
        mock_attempt_adoption.assert_called_once()
        mock_create_user.assert_called_once()
        assert mock_create_user.call_args.kwargs["privy_user_id"] == TEST_DID


class TestAdoptionNotCalledWhenItShouldNotBe:
    @patch("src.routes.auth.attempt_adoption", new_callable=AsyncMock)
    @patch("src.routes.auth.migration_mode_is_adopt", return_value=False)
    @patch("src.routes.auth.users_module.get_user_by_email", return_value=None)
    @patch("src.routes.auth.supabase_config.get_supabase_client")
    @patch("src.routes.auth._generate_unique_username")
    @patch("src.routes.auth.users_module.create_enhanced_user")
    @patch("src.routes.auth.users_module.get_user_by_privy_id", return_value=None)
    @patch("src.routes.auth.get_cached_user_by_privy_id", return_value=None)
    def test_migration_mode_off_never_calls_adoption(
        self,
        mock_cached_privy,
        mock_get_by_privy_id,
        mock_create_user,
        mock_gen_username,
        mock_get_client,
        mock_get_by_email,
        mock_mode,
        mock_attempt_adoption,
        monkeypatch,
        key_pair,
    ):
        private_key = _configure_token(monkeypatch, key_pair)
        token = _mint_token(private_key)
        mock_gen_username.return_value = "newappuser123"
        mock_create_user.return_value = _NEW_ACCOUNT_RESULT

        response = client.post("/auth", json=_auth_body(token=token))

        assert response.status_code == 200
        mock_attempt_adoption.assert_not_called()

    @patch("src.routes.auth.users_module.create_enhanced_user")
    @patch("src.routes.auth._generate_unique_username")
    @patch("src.routes.auth.supabase_config.get_supabase_client")
    @patch("src.routes.auth.users_module.get_user_by_email", return_value=None)
    @patch("src.routes.auth.attempt_adoption", new_callable=AsyncMock)
    @patch("src.routes.auth.migration_mode_is_adopt", return_value=True)
    @patch("src.routes.auth.get_cached_user_by_username", return_value=None)
    @patch("src.routes.auth.users_module.get_user_by_username", return_value=None)
    @patch("src.routes.auth.users_module.get_user_by_privy_id", return_value=None)
    @patch("src.routes.auth.get_cached_user_by_privy_id", return_value=None)
    def test_unverified_token_never_calls_adoption(
        self,
        mock_cached_privy,
        mock_get_by_privy_id,
        mock_get_by_username,
        mock_cached_username,
        mock_mode,
        mock_attempt_adoption,
        mock_get_by_email,
        mock_get_client,
        mock_gen_username,
        mock_create_user,
        monkeypatch,
        key_pair,
    ):
        # "log" mode + no token -> the request reaches account lookup
        # unverified, which must never trigger adoption regardless of mode.
        _configure_token(monkeypatch, key_pair, mode="log")
        mock_gen_username.return_value = "wallet-user"
        mock_create_user.return_value = {**_NEW_ACCOUNT_RESULT, "user_id": 902}

        response = client.post("/auth", json=_auth_body())

        assert response.status_code == 200
        mock_attempt_adoption.assert_not_called()
