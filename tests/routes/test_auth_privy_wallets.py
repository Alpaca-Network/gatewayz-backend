"""Tests for ingesting verified Privy wallet linked-accounts into
user_wallets on POST /auth (gatewayz-backend#2251).

Mirrors tests/routes/test_auth_privy_token.py's TestClient + real ES256
token pattern. `_ingest_privy_wallets` is unit-tested directly (it's the
function that actually decides what gets linked); a couple of route-level
tests prove it's wired into both the existing-user and new-user branches of
`privy_auth`.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from src.main import app
from src.routes.auth import PrivyAuthResponse, _ingest_privy_wallets
from src.schemas.auth import PrivyLinkedAccount

client = TestClient(app)

TEST_APP_ID = "test-app-id"
TEST_DID = "did:privy:cliduser123"
WALLET_1 = "0x" + "a1" * 20
WALLET_2 = "0x" + "b2" * 20


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


def _wallet_account(
    address=WALLET_1, *, type="wallet", chain_type="ethereum", wallet_client_type="privy"
):
    return PrivyLinkedAccount(
        type=type,
        address=address,
        chain_type=chain_type,
        wallet_client_type=wallet_client_type,
    )


def _auth_body(*, token=None, linked_accounts=None):
    body = {
        "user": {
            "id": TEST_DID,
            "created_at": 1700000000,
            "linked_accounts": linked_accounts or [],
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


# ---------------------------------------------------------------------------
# Unit tests for _ingest_privy_wallets
# ---------------------------------------------------------------------------


class TestIngestPrivyWallets:
    def test_unverified_token_links_nothing(self):
        with patch("src.routes.auth.link_wallet") as mock_link:
            count = _ingest_privy_wallets(1, [_wallet_account()], verified=False)

        assert count == 0
        mock_link.assert_not_called()

    @patch("src.routes.auth.link_wallet")
    @patch("src.routes.auth.get_wallet", return_value=None)
    @patch("src.routes.auth.count_wallets", return_value=0)
    def test_verified_single_wallet_is_linked_as_primary(
        self, mock_count, mock_get_wallet, mock_link
    ):
        mock_link.return_value = {"id": 1, "wallet_address": WALLET_1}

        count = _ingest_privy_wallets(
            42, [_wallet_account(WALLET_1, wallet_client_type="privy")], verified=True
        )

        assert count == 1
        mock_link.assert_called_once_with(
            42, WALLET_1.lower(), source="privy", wallet_client_type="privy", make_primary=True
        )

    @patch("src.routes.auth.link_wallet")
    @patch("src.routes.auth.get_wallet", return_value=None)
    @patch("src.routes.auth.count_wallets", return_value=0)
    def test_two_wallets_only_first_is_primary(self, mock_count, mock_get_wallet, mock_link):
        mock_link.side_effect = [
            {"id": 1, "wallet_address": WALLET_1},
            {"id": 2, "wallet_address": WALLET_2},
        ]
        accounts = [
            _wallet_account(WALLET_1, type="wallet", wallet_client_type="privy"),
            _wallet_account(WALLET_2, type="wallet", wallet_client_type="metamask"),
        ]

        count = _ingest_privy_wallets(42, accounts, verified=True)

        assert count == 2
        assert mock_link.call_args_list[0].kwargs["make_primary"] is True
        assert mock_link.call_args_list[1].kwargs["make_primary"] is False
        assert mock_link.call_args_list[1].kwargs["wallet_client_type"] == "metamask"

    @patch("src.routes.auth.link_wallet")
    @patch("src.routes.auth.count_wallets", return_value=1)
    def test_user_already_has_a_wallet_new_one_is_not_primary(self, mock_count, mock_link):
        mock_link.return_value = {"id": 3, "wallet_address": WALLET_1}
        with patch("src.routes.auth.get_wallet", return_value=None):
            _ingest_privy_wallets(42, [_wallet_account(WALLET_1)], verified=True)

        assert mock_link.call_args.kwargs["make_primary"] is False

    @patch("src.routes.auth.link_wallet")
    @patch("src.routes.auth.get_wallet", return_value=None)
    @patch("src.routes.auth.count_wallets", return_value=0)
    def test_smart_wallet_type_is_ingested(self, mock_count, mock_get_wallet, mock_link):
        mock_link.return_value = {"id": 1, "wallet_address": WALLET_1}

        count = _ingest_privy_wallets(
            42, [_wallet_account(WALLET_1, type="smart_wallet")], verified=True
        )

        assert count == 1
        mock_link.assert_called_once()

    @patch("src.routes.auth.link_wallet")
    def test_non_ethereum_chain_type_is_skipped(self, mock_link):
        account = _wallet_account(WALLET_1, chain_type="solana")

        count = _ingest_privy_wallets(42, [account], verified=True)

        assert count == 0
        mock_link.assert_not_called()

    @patch("src.routes.auth.link_wallet")
    @patch("src.routes.auth.count_wallets", return_value=0)
    def test_invalid_address_is_skipped_without_raising(self, mock_count, mock_link):
        account = _wallet_account("not-an-address")

        count = _ingest_privy_wallets(42, [account], verified=True)

        assert count == 0
        mock_link.assert_not_called()

    @patch("src.routes.auth.link_wallet")
    @patch("src.routes.auth.get_wallet")
    @patch("src.routes.auth.count_wallets", return_value=0)
    def test_wallet_owned_by_another_user_is_skipped_and_logged(
        self, mock_count, mock_get_wallet, mock_link, caplog
    ):
        mock_get_wallet.return_value = {"user_id": 999, "wallet_address": WALLET_1}

        with caplog.at_level("WARNING", logger="src.routes.auth"):
            count = _ingest_privy_wallets(42, [_wallet_account(WALLET_1)], verified=True)

        assert count == 0
        mock_link.assert_not_called()
        assert "privy_auth.wallet_conflict" in caplog.text

    @patch("src.routes.auth.link_wallet")
    @patch("src.routes.auth.get_wallet")
    @patch("src.routes.auth.count_wallets", return_value=1)
    def test_wallet_already_owned_by_caller_is_a_noop(self, mock_count, mock_get_wallet, mock_link):
        mock_get_wallet.return_value = {"user_id": 42, "wallet_address": WALLET_1}

        count = _ingest_privy_wallets(42, [_wallet_account(WALLET_1)], verified=True)

        assert count == 0
        mock_link.assert_not_called()

    @patch("src.routes.auth.link_wallet", side_effect=RuntimeError("db exploded"))
    @patch("src.routes.auth.get_wallet", return_value=None)
    @patch("src.routes.auth.count_wallets", return_value=0)
    def test_link_wallet_raising_never_propagates(self, mock_count, mock_get_wallet, mock_link):
        count = _ingest_privy_wallets(42, [_wallet_account(WALLET_1)], verified=True)

        assert count == 0

    @patch("src.routes.auth.link_wallet")
    @patch("src.routes.auth.count_wallets", return_value=0)
    def test_non_wallet_account_types_are_ignored(self, mock_count, mock_link):
        account = PrivyLinkedAccount(type="email", email="user@example.com")

        count = _ingest_privy_wallets(42, [account], verified=True)

        assert count == 0
        mock_link.assert_not_called()


# ---------------------------------------------------------------------------
# Schema: camelCase Privy fields
# ---------------------------------------------------------------------------


class TestPrivyLinkedAccountWalletFields:
    def test_camel_case_fields_are_accepted(self):
        account = PrivyLinkedAccount.model_validate(
            {
                "type": "wallet",
                "address": WALLET_1,
                "chainType": "ethereum",
                "walletClientType": "metamask",
                "connectorType": "injected",
            }
        )

        assert account.chain_type == "ethereum"
        assert account.wallet_client_type == "metamask"
        assert account.connector_type == "injected"

    def test_snake_case_fields_are_also_accepted(self):
        account = PrivyLinkedAccount.model_validate(
            {
                "type": "wallet",
                "address": WALLET_1,
                "chain_type": "ethereum",
                "wallet_client_type": "privy",
            }
        )

        assert account.chain_type == "ethereum"
        assert account.wallet_client_type == "privy"


# ---------------------------------------------------------------------------
# Route-level wiring: prove privy_auth actually calls _ingest_privy_wallets
# ---------------------------------------------------------------------------


class TestWalletIngestionWiring:
    @patch("src.routes.auth._handle_existing_user")
    @patch("src.routes.auth.get_cached_user_by_privy_id")
    def test_verified_token_existing_user_ingests_wallets(
        self, mock_get_cached_user, mock_handle_existing_user, monkeypatch, key_pair
    ):
        private_key = _configure(monkeypatch, key_pair, mode="enforce")
        token = _mint_token(private_key)
        mock_get_cached_user.return_value = {"id": 99, "privy_user_id": TEST_DID}
        mock_handle_existing_user.return_value = PrivyAuthResponse(
            success=True, message="Login successful", user_id=99
        )

        response = client.post(
            "/auth",
            json=_auth_body(
                token=token,
                linked_accounts=[
                    {
                        "type": "wallet",
                        "address": WALLET_1,
                        "chainType": "ethereum",
                        "walletClientType": "privy",
                    }
                ],
            ),
        )

        assert response.status_code == 200
        # Wiring lives inside _handle_existing_user; here we only prove
        # privy_auth reaches that call at all with a verified token and does
        # not short-circuit before it (the ingest helper itself is unit
        # tested above via _ingest_privy_wallets directly).
        mock_handle_existing_user.assert_called_once()
        assert mock_handle_existing_user.call_args.kwargs["token_verified"] is True

    @patch("src.routes.auth._handle_existing_user")
    @patch("src.routes.auth.get_cached_user_by_privy_id")
    def test_log_mode_without_token_passes_unverified(
        self, mock_get_cached_user, mock_handle_existing_user, monkeypatch, key_pair
    ):
        _configure(monkeypatch, key_pair, mode="log")
        mock_get_cached_user.return_value = {"id": 99, "privy_user_id": TEST_DID}
        mock_handle_existing_user.return_value = PrivyAuthResponse(
            success=True, message="Login successful", user_id=99
        )

        response = client.post(
            "/auth",
            json=_auth_body(
                linked_accounts=[{"type": "wallet", "address": WALLET_1, "chainType": "ethereum"}]
            ),
        )

        assert response.status_code == 200
        assert mock_handle_existing_user.call_args.kwargs["token_verified"] is False

    @patch("src.routes.auth.link_wallet")
    @patch("src.routes.auth.get_wallet", return_value=None)
    @patch("src.routes.auth.count_wallets", return_value=0)
    @patch("src.routes.auth.users_module.get_user_by_email", return_value=None)
    @patch("src.routes.auth.supabase_config.get_supabase_client")
    @patch("src.routes.auth._generate_unique_username")
    @patch("src.routes.auth.users_module.create_enhanced_user")
    @patch("src.routes.auth.get_cached_user_by_username")
    @patch("src.routes.auth.users_module.get_user_by_privy_id", return_value=None)
    @patch("src.routes.auth.get_cached_user_by_privy_id", return_value=None)
    def test_new_user_with_verified_token_links_wallet(
        self,
        mock_cached_privy,
        mock_get_by_privy_id,
        mock_cached_username,
        mock_create_user,
        mock_gen_username,
        mock_get_client,
        mock_get_by_email,
        mock_count,
        mock_get_wallet,
        mock_link,
        monkeypatch,
        key_pair,
    ):
        private_key = _configure(monkeypatch, key_pair, mode="enforce")
        token = _mint_token(private_key)
        mock_gen_username.return_value = "wallet-user"
        mock_create_user.return_value = {
            "user_id": 501,
            "username": "wallet-user",
            "credits": 0,
            "primary_api_key": "gw_test_new_501",
            "subscription_status": "inactive",
            "tier": "basic",
            "trial_expires_at": None,
            "subscription_end_date": None,
        }
        mock_link.return_value = {"id": 1, "wallet_address": WALLET_1}

        response = client.post(
            "/auth",
            json=_auth_body(
                token=token,
                linked_accounts=[
                    {
                        "type": "wallet",
                        "address": WALLET_1,
                        "chainType": "ethereum",
                        "walletClientType": "privy",
                    }
                ],
            ),
        )

        assert response.status_code == 200
        assert response.json()["is_new_user"] is True
        mock_link.assert_called_once_with(
            501, WALLET_1.lower(), source="privy", wallet_client_type="privy", make_primary=True
        )
