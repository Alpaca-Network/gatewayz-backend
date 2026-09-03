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


class TestEmailUsernameCollisionTakeover:
    """
    gatewayz-backend#2248 security review, "Fix round 1": `request.email` is
    unauthenticated client input, not part of the verified token's claims. It
    must never be used to locate, rebind, or return the API key of someone
    else's account. See src.routes.auth around the `token_verified` guards.
    """

    ATTACKER_SUB = "did:privy:attacker"
    VICTIM_PRIVY_ID = "did:privy:realvictim"

    def _new_account_mocks(self, mock_gen_username, mock_create_user, *, resolved_username):
        mock_gen_username.return_value = resolved_username
        mock_create_user.return_value = {
            "user_id": 501,
            "username": resolved_username,
            "credits": 0,
            "primary_api_key": "gw_test_new_501",
            "subscription_status": "inactive",
            "tier": "basic",
            "trial_expires_at": None,
            "subscription_end_date": None,
        }

    @patch("src.routes.auth.users_module.get_user_by_email")
    @patch("src.routes.auth.supabase_config.get_supabase_client")
    @patch("src.routes.auth._generate_unique_username")
    @patch("src.routes.auth.users_module.create_enhanced_user")
    @patch("src.routes.auth.get_cached_user_by_username")
    @patch("src.routes.auth.users_module.get_user_by_privy_id")
    @patch("src.routes.auth.get_cached_user_by_privy_id")
    def test_attacker_email_colliding_with_victim_username_creates_new_account(
        self,
        mock_cached_privy,
        mock_get_by_privy_id,
        mock_cached_username,
        mock_create_user,
        mock_gen_username,
        mock_get_client,
        mock_get_by_email,
        monkeypatch,
        key_pair,
    ):
        """Attacker's own valid token + email whose local-part == victim's
        username must NOT touch the victim's row at all (a)."""
        private_key = _configure(monkeypatch, key_pair, mode="enforce")
        token = _mint_token(private_key, sub=self.ATTACKER_SUB)

        mock_cached_privy.return_value = None
        mock_get_by_privy_id.return_value = None
        mock_get_by_email.return_value = None  # attacker's own (fake) email is unowned
        self._new_account_mocks(
            mock_gen_username, mock_create_user, resolved_username="victim_9f3a"
        )

        body = _auth_body(token=token)
        body["user"]["id"] = self.ATTACKER_SUB
        body[
            "email"
        ] = "victim@attacker-controlled.example"  # local-part collides with victim's username

        response = client.post("/auth", json=body)

        assert response.status_code == 200
        assert response.json()["user_id"] == 501  # the new account, not the victim's
        assert response.json()["is_new_user"] is True
        # The username-fallback lookup must never have been touched.
        mock_cached_username.assert_not_called()
        mock_create_user.assert_called_once()
        assert mock_create_user.call_args.kwargs["privy_user_id"] == self.ATTACKER_SUB

    @patch("src.routes.auth.users_module.get_user_by_email")
    @patch("src.routes.auth.supabase_config.get_supabase_client")
    @patch("src.routes.auth._generate_unique_username")
    @patch("src.routes.auth.users_module.create_enhanced_user")
    @patch("src.routes.auth.get_cached_user_by_username")
    @patch("src.routes.auth.users_module.get_user_by_privy_id")
    @patch("src.routes.auth.get_cached_user_by_privy_id")
    def test_attacker_email_equal_to_victim_email_falls_back_to_placeholder(
        self,
        mock_cached_privy,
        mock_get_by_privy_id,
        mock_cached_username,
        mock_create_user,
        mock_gen_username,
        mock_get_client,
        mock_get_by_email,
        monkeypatch,
        key_pair,
        caplog,
    ):
        """Attacker's own valid token + email == victim's actual email must NOT
        claim the victim's account; a placeholder email is used instead (b)."""
        private_key = _configure(monkeypatch, key_pair, mode="enforce")
        token = _mint_token(private_key, sub=self.ATTACKER_SUB)

        mock_cached_privy.return_value = None
        mock_get_by_privy_id.return_value = None
        mock_get_by_email.return_value = {
            "id": 42,
            "privy_user_id": self.VICTIM_PRIVY_ID,
            "email": "victim@example.com",
        }
        self._new_account_mocks(mock_gen_username, mock_create_user, resolved_username="victim")

        body = _auth_body(token=token)
        body["user"]["id"] = self.ATTACKER_SUB
        body["email"] = "victim@example.com"

        with caplog.at_level("WARNING", logger="src.routes.auth"):
            response = client.post("/auth", json=body)

        assert response.status_code == 200
        assert response.json()["user_id"] == 501
        mock_create_user.assert_called_once()
        used_email = mock_create_user.call_args.kwargs["email"]
        assert used_email != "victim@example.com"
        assert used_email.startswith("noemail+")
        assert "privy_auth.legacy_account_claim_blocked" in caplog.text

    @patch("src.routes.auth._handle_existing_user")
    @patch("src.routes.auth.get_cached_user_by_username")
    @patch("src.routes.auth.users_module.get_user_by_privy_id")
    @patch("src.routes.auth.supabase_config.get_supabase_client")
    @patch("src.routes.auth.get_cached_user_by_privy_id")
    def test_log_mode_never_rebinds_a_different_existing_privy_id(
        self,
        mock_cached_privy,
        mock_get_client,
        mock_get_by_privy_id,
        mock_cached_username,
        mock_handle_existing_user,
        monkeypatch,
        key_pair,
        caplog,
    ):
        """In log mode (unverified request), a username match on an account that
        already has a *different* privy_user_id must never be rebound (c)."""
        _configure(monkeypatch, key_pair, mode="log")

        mock_cached_privy.return_value = None
        mock_get_by_privy_id.return_value = None
        mock_cached_username.return_value = {
            "id": 42,
            "username": "wallet-user",
            "privy_user_id": self.VICTIM_PRIVY_ID,
        }
        mock_handle_existing_user.return_value = PrivyAuthResponse(
            success=True, message="Login successful", user_id=42
        )

        with caplog.at_level("WARNING", logger="src.routes.auth"):
            response = client.post("/auth", json=_auth_body())  # no token -> log mode, unverified

        assert response.status_code == 200
        assert "privy_auth.rebind_blocked" in caplog.text
        # The row handed to _handle_existing_user must be untouched: its
        # privy_user_id must still be the victim's, never the caller's.
        passed_user = mock_handle_existing_user.call_args.kwargs["existing_user"]
        assert passed_user["privy_user_id"] == self.VICTIM_PRIVY_ID

    @patch("src.routes.auth.users_module.get_user_by_email")
    @patch("src.routes.auth.supabase_config.get_supabase_client")
    @patch("src.routes.auth._generate_unique_username")
    @patch("src.routes.auth.users_module.create_enhanced_user")
    @patch("src.routes.auth.get_cached_user_by_username")
    @patch("src.routes.auth.users_module.get_user_by_privy_id")
    @patch("src.routes.auth.get_cached_user_by_privy_id")
    def test_log_mode_never_claims_a_null_privy_id_legacy_account(
        self,
        mock_cached_privy,
        mock_get_by_privy_id,
        mock_cached_username,
        mock_create_user,
        mock_gen_username,
        mock_get_client,
        mock_get_by_email,
        monkeypatch,
        key_pair,
        caplog,
    ):
        """gatewayz-backend#2248 review, "Fix round 2": in log mode (no token,
        hence unverified), a username match on a legacy account that has never
        linked Privy (privy_user_id IS NULL) must not be rebound or have its key
        returned — reproduces the reviewer's live repro. A new account is
        created instead, exactly as the verified-token path already does."""
        _configure(monkeypatch, key_pair, mode="log")

        mock_cached_privy.return_value = None
        mock_get_by_privy_id.return_value = None
        mock_cached_username.return_value = {
            "id": 42,
            "username": "wallet-user",
            "privy_user_id": None,
        }
        mock_get_by_email.return_value = None
        self._new_account_mocks(
            mock_gen_username, mock_create_user, resolved_username="wallet-user_ab12"
        )

        with caplog.at_level("WARNING", logger="src.routes.auth"):
            response = client.post("/auth", json=_auth_body())  # no token -> log mode, unverified

        assert response.status_code == 200
        assert response.json()["is_new_user"] is True
        assert response.json()["user_id"] == 501  # the new account, not user 42's
        assert "privy_auth.legacy_account_claim_blocked" in caplog.text
        mock_create_user.assert_called_once()
        assert mock_create_user.call_args.kwargs["privy_user_id"] == TEST_DID
