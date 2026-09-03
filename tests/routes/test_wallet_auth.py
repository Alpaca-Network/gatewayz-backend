"""Tests for src.routes.wallet_auth (gatewayz-backend#2249 #2250 #2251 #2252)."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from fastapi.testclient import TestClient

from src.main import app
from src.security.deps import get_user_id
from src.security.siwe import build_siwe_message
from src.services.auth_rate_limiting import AuthRateLimitResult

client = TestClient(app)

_ISSUED_AT = datetime(2026, 9, 3, 12, 0, 0, tzinfo=UTC)
_ALLOWED = AuthRateLimitResult(allowed=True, remaining=10)
_TEST_USER_ID = 7


@pytest.fixture(autouse=True)
def _override_user_id():
    """Set the get_user_id override immediately before each test rather
    than once at module import. Tests run under pytest-xdist (-n auto, see
    pytest.ini), and other route test modules (e.g. test_faucet.py) mutate
    this SAME shared `app.dependency_overrides` dict at their own import
    time -- a bare module-level assignment here is a coin flip on whichever
    file's import happened last in a given worker process. Deliberately NOT
    torn down after: other modules' own bare module-level overrides (set
    once at collection, never re-applied) rely on this dict staying
    populated for the rest of the worker's test session."""
    app.dependency_overrides[get_user_id] = lambda: _TEST_USER_ID


def _sign(account, message: str) -> str:
    sig = account.sign_message(encode_defunct(text=message)).signature.hex()
    return sig if sig.startswith("0x") else f"0x{sig}"


def _login_message(address, nonce="deadbeef", chain_id=43113) -> str:
    return build_siwe_message(
        address=address,
        nonce=nonce,
        chain_id=chain_id,
        statement="Sign in to Gatewayz.",
        issued_at=_ISSUED_AT,
    )


def _link_message(address, user_id, nonce="cafebabe", chain_id=43113) -> str:
    return build_siwe_message(
        address=address,
        nonce=nonce,
        chain_id=chain_id,
        statement=f"Link this wallet to Gatewayz account {user_id}.",
        issued_at=_ISSUED_AT,
    )


def _redis(getdel_return=None):
    redis_client = MagicMock()
    redis_client.getdel.return_value = getdel_return
    return redis_client


# ---------------------------------------------------------------------------
# POST /auth/wallet/nonce
# ---------------------------------------------------------------------------


@patch("src.routes.wallet_auth.check_auth_rate_limit", new_callable=AsyncMock)
@patch("src.routes.wallet_auth.get_redis_client")
def test_nonce_returns_message_and_expiry(mock_get_redis, mock_rl):
    mock_rl.return_value = _ALLOWED
    redis_client = _redis()
    mock_get_redis.return_value = redis_client

    account = Account.create()
    response = client.post("/auth/wallet/nonce", json={"wallet_address": account.address})

    assert response.status_code == 200
    data = response.json()["data"]
    assert "Sign in to Gatewayz." in data["message"]
    assert data["expires_in"] == 300
    args, _ = redis_client.setex.call_args
    assert args[0] == f"siwe_nonce:login:{account.address.lower()}"


@patch("src.routes.wallet_auth.check_auth_rate_limit", new_callable=AsyncMock)
@patch("src.routes.wallet_auth.get_redis_client")
def test_nonce_normalizes_uppercase_address_and_checksums_message(mock_get_redis, mock_rl):
    mock_rl.return_value = _ALLOWED
    redis_client = _redis()
    mock_get_redis.return_value = redis_client

    account = Account.create()
    response = client.post(
        "/auth/wallet/nonce", json={"wallet_address": account.address.upper().replace("0X", "0x")}
    )

    assert response.status_code == 200
    key_arg, _ = redis_client.setex.call_args[0], None
    assert redis_client.setex.call_args[0][0] == f"siwe_nonce:login:{account.address.lower()}"
    assert account.address in response.json()["data"]["message"]


@patch("src.routes.wallet_auth.check_auth_rate_limit", new_callable=AsyncMock)
@patch("src.routes.wallet_auth.get_redis_client")
def test_nonce_rejects_disallowed_chain_id(mock_get_redis, mock_rl):
    mock_rl.return_value = _ALLOWED
    mock_get_redis.return_value = _redis()

    account = Account.create()
    response = client.post(
        "/auth/wallet/nonce", json={"wallet_address": account.address, "chain_id": 999}
    )

    assert response.status_code == 422
    # The global HTTPException handler masks 422 detail text into a
    # generic envelope (pre-existing app-wide behavior, not specific to
    # this route) -- the status code is the contract clients can rely on.


@patch("src.routes.wallet_auth.check_auth_rate_limit", new_callable=AsyncMock)
def test_nonce_honours_rate_limit(mock_rl):
    mock_rl.return_value = AuthRateLimitResult(allowed=False, remaining=0, retry_after=60)

    account = Account.create()
    response = client.post("/auth/wallet/nonce", json={"wallet_address": account.address})

    assert response.status_code == 429


@patch("src.routes.wallet_auth.check_auth_rate_limit", new_callable=AsyncMock)
@patch("src.routes.wallet_auth.get_redis_client")
def test_nonce_returns_503_when_redis_unavailable(mock_get_redis, mock_rl):
    mock_rl.return_value = _ALLOWED
    mock_get_redis.return_value = None

    account = Account.create()
    response = client.post("/auth/wallet/nonce", json={"wallet_address": account.address})

    assert response.status_code == 503


def test_nonce_rejects_malformed_address():
    response = client.post("/auth/wallet/nonce", json={"wallet_address": "not-an-address"})
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# POST /auth/wallet/verify
# ---------------------------------------------------------------------------


@patch("src.routes.wallet_auth.link_wallet")
@patch("src.routes.wallet_auth.create_api_key")
@patch("src.routes.wallet_auth.resolve_key_environment")
@patch("src.routes.wallet_auth._generate_unique_username")
@patch("src.routes.wallet_auth.supabase_config")
@patch("src.routes.wallet_auth.get_wallet")
@patch("src.routes.wallet_auth.check_auth_rate_limit", new_callable=AsyncMock)
@patch("src.routes.wallet_auth.get_redis_client")
def test_verify_new_wallet_signs_up_and_provisions_key(
    mock_get_redis,
    mock_rl,
    mock_get_wallet,
    mock_supabase_config,
    mock_unique_username,
    mock_resolve_env,
    mock_create_api_key,
    mock_link_wallet,
):
    mock_rl.return_value = _ALLOWED
    account = Account.create()
    message = _login_message(account.address)
    mock_get_redis.return_value = _redis(getdel_return=message)
    mock_get_wallet.return_value = None
    mock_unique_username.return_value = f"wallet_{account.address[2:8]}".lower()

    insert_result = MagicMock()
    insert_result.data = [
        {
            "id": 99,
            "username": mock_unique_username.return_value,
            "email": f"wallet+{account.address.lower()}@wallet.placeholder",
        }
    ]
    fake_client = MagicMock()
    fake_client.table.return_value.insert.return_value.execute.return_value = insert_result
    mock_supabase_config.get_supabase_client.return_value = fake_client

    mock_resolve_env.return_value = ("test", True)
    mock_create_api_key.return_value = ("gw_test_newkey", 1)
    mock_link_wallet.return_value = {"wallet_address": account.address.lower()}

    response = client.post(
        "/auth/wallet/verify",
        json={
            "wallet_address": account.address,
            "message": message,
            "signature": _sign(account, message),
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["is_new_user"] is True
    assert body["user_id"] == 99
    assert body["api_key"] == "gw_test_newkey"
    assert body["auth_method"] == "wallet"
    assert body["privy_user_id"] is None

    _, kwargs = mock_create_api_key.call_args
    assert kwargs["key_name"] == "Wallet Sign-In"
    assert kwargs["is_primary"] is True
    assert kwargs["user_id"] == 99

    args, kwargs = mock_link_wallet.call_args
    assert args[0] == 99
    assert args[1] == account.address.lower()
    assert kwargs["source"] == "siwe"
    assert kwargs["make_primary"] is True


@patch("src.routes.wallet_auth._handle_existing_user")
@patch("src.routes.wallet_auth.get_user_api_keys")
@patch("src.routes.wallet_auth.users_module")
@patch("src.routes.wallet_auth.get_wallet")
@patch("src.routes.wallet_auth.check_auth_rate_limit", new_callable=AsyncMock)
@patch("src.routes.wallet_auth.get_redis_client")
def test_verify_existing_wallet_logs_in_without_creating_a_user(
    mock_get_redis,
    mock_rl,
    mock_get_wallet,
    mock_users_module,
    mock_get_user_api_keys,
    mock_handle_existing,
):
    mock_rl.return_value = _ALLOWED
    account = Account.create()
    message = _login_message(account.address)
    mock_get_redis.return_value = _redis(getdel_return=message)
    mock_get_wallet.return_value = {"user_id": 5, "wallet_address": account.address.lower()}
    mock_users_module.get_user_by_id.return_value = {"id": 5, "username": "existing"}
    mock_get_user_api_keys.return_value = [
        {
            "is_primary": True,
            "is_active": True,
            "api_key": "gw_live_current",
            "expiration_date": None,
        }
    ]

    from src.schemas import AuthMethod, PrivyAuthResponse

    mock_handle_existing.return_value = PrivyAuthResponse(
        success=True,
        message="Login successful",
        user_id=5,
        api_key="gw_live_current",
        auth_method=AuthMethod.WALLET,
        is_new_user=False,
    )

    response = client.post(
        "/auth/wallet/verify",
        json={
            "wallet_address": account.address,
            "message": message,
            "signature": _sign(account, message),
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["is_new_user"] is False
    assert body["api_key"] == "gw_live_current"
    assert body["privy_user_id"] is None
    mock_handle_existing.assert_called_once()
    _, kwargs = mock_handle_existing.call_args
    assert kwargs["auth_method"] == AuthMethod.WALLET


@patch("src.routes.wallet_auth._handle_existing_user")
@patch("src.routes.wallet_auth.create_api_key")
@patch("src.routes.wallet_auth.resolve_key_environment")
@patch("src.routes.wallet_auth.update_api_key")
@patch("src.routes.wallet_auth.get_user_api_keys")
@patch("src.routes.wallet_auth.users_module")
@patch("src.routes.wallet_auth.get_wallet")
@patch("src.routes.wallet_auth.check_auth_rate_limit", new_callable=AsyncMock)
@patch("src.routes.wallet_auth.get_redis_client")
def test_verify_mints_replacement_key_when_primary_is_expired(
    mock_get_redis,
    mock_rl,
    mock_get_wallet,
    mock_users_module,
    mock_get_user_api_keys,
    mock_update_api_key,
    mock_resolve_env,
    mock_create_api_key,
    mock_handle_existing,
):
    mock_rl.return_value = _ALLOWED
    account = Account.create()
    message = _login_message(account.address)
    mock_get_redis.return_value = _redis(getdel_return=message)
    mock_get_wallet.return_value = {"user_id": 5, "wallet_address": account.address.lower()}
    mock_users_module.get_user_by_id.return_value = {"id": 5, "username": "existing"}
    mock_get_user_api_keys.return_value = [
        {
            "is_primary": True,
            "is_active": True,
            "api_key": "gw_live_expired",
            "expiration_date": "2020-01-01T00:00:00Z",
        }
    ]
    mock_resolve_env.return_value = ("live", False)
    mock_create_api_key.return_value = ("gw_live_fresh", 2)

    from src.schemas import AuthMethod, PrivyAuthResponse

    mock_handle_existing.return_value = PrivyAuthResponse(
        success=True,
        message="Login successful",
        user_id=5,
        api_key="gw_live_fresh",
        auth_method=AuthMethod.WALLET,
        is_new_user=False,
    )

    response = client.post(
        "/auth/wallet/verify",
        json={
            "wallet_address": account.address,
            "message": message,
            "signature": _sign(account, message),
        },
    )

    assert response.status_code == 200
    mock_update_api_key.assert_called_once_with("gw_live_expired", 5, {"is_active": False})
    _, kwargs = mock_create_api_key.call_args
    assert kwargs["is_primary"] is True
    assert kwargs["expiration_days"] is not None


@patch("src.routes.wallet_auth.check_auth_rate_limit", new_callable=AsyncMock)
@patch("src.routes.wallet_auth.get_redis_client")
def test_verify_rejects_tampered_message(mock_get_redis, mock_rl):
    mock_rl.return_value = _ALLOWED
    account = Account.create()
    message = _login_message(account.address)
    mock_get_redis.return_value = _redis(getdel_return=message)

    response = client.post(
        "/auth/wallet/verify",
        json={
            "wallet_address": account.address,
            "message": message + " tampered",
            "signature": _sign(account, message),
        },
    )

    assert response.status_code == 400
    assert response.json()["error"]["context"]["parameter_value"] == "nonce_missing_or_expired"


@patch("src.routes.wallet_auth.check_auth_rate_limit", new_callable=AsyncMock)
@patch("src.routes.wallet_auth.get_redis_client")
def test_verify_rejects_when_nonce_missing_or_already_consumed(mock_get_redis, mock_rl):
    """GETDEL is atomic -- a second verify (replay) sees no stored nonce,
    same as an expired one."""
    mock_rl.return_value = _ALLOWED
    account = Account.create()
    message = _login_message(account.address)
    mock_get_redis.return_value = _redis(getdel_return=None)

    response = client.post(
        "/auth/wallet/verify",
        json={
            "wallet_address": account.address,
            "message": message,
            "signature": _sign(account, message),
        },
    )

    assert response.status_code == 400
    assert response.json()["error"]["context"]["parameter_value"] == "nonce_missing_or_expired"


@patch("src.routes.wallet_auth.check_auth_rate_limit", new_callable=AsyncMock)
@patch("src.routes.wallet_auth.get_redis_client")
def test_verify_rejects_wrong_signer_as_address_mismatch(mock_get_redis, mock_rl):
    mock_rl.return_value = _ALLOWED
    signer = Account.create()
    claimed = Account.create()
    # Message is server-authored for the CLAIMED address, but actually
    # signed by a different key.
    message = _login_message(claimed.address)
    mock_get_redis.return_value = _redis(getdel_return=message)

    response = client.post(
        "/auth/wallet/verify",
        json={
            "wallet_address": claimed.address,
            "message": message,
            "signature": _sign(signer, message),
        },
    )

    assert response.status_code == 401
    assert "signature_address_mismatch" in response.json()["error"]["detail"]


@patch("src.routes.wallet_auth.check_auth_rate_limit", new_callable=AsyncMock)
@patch("src.routes.wallet_auth.get_redis_client")
def test_verify_rejects_garbage_signature_as_invalid_signature(mock_get_redis, mock_rl):
    mock_rl.return_value = _ALLOWED
    account = Account.create()
    message = _login_message(account.address)
    mock_get_redis.return_value = _redis(getdel_return=message)

    response = client.post(
        "/auth/wallet/verify",
        json={
            "wallet_address": account.address,
            "message": message,
            "signature": "0xnotasignature",
        },
    )

    assert response.status_code == 401
    assert "invalid_signature" in response.json()["error"]["detail"]


@patch("src.routes.wallet_auth.check_auth_rate_limit", new_callable=AsyncMock)
def test_verify_honours_login_rate_limit(mock_rl):
    mock_rl.return_value = AuthRateLimitResult(allowed=False, remaining=0, retry_after=30)

    account = Account.create()
    message = _login_message(account.address)
    response = client.post(
        "/auth/wallet/verify",
        json={
            "wallet_address": account.address,
            "message": message,
            "signature": _sign(account, message),
        },
    )

    assert response.status_code == 429


@patch("src.routes.wallet_auth.get_wallet")
@patch("src.routes.wallet_auth.check_auth_rate_limit", new_callable=AsyncMock)
@patch("src.routes.wallet_auth.get_redis_client")
def test_verify_honours_register_rate_limit_on_new_user_branch(
    mock_get_redis, mock_rl, mock_get_wallet
):
    from src.services.auth_rate_limiting import AuthRateLimitType

    account = Account.create()
    message = _login_message(account.address)
    mock_get_redis.return_value = _redis(getdel_return=message)
    mock_get_wallet.return_value = None

    async def _side_effect(_identifier, limit_type):
        if limit_type == AuthRateLimitType.REGISTER:
            return AuthRateLimitResult(allowed=False, remaining=0, retry_after=15)
        return _ALLOWED

    mock_rl.side_effect = _side_effect

    response = client.post(
        "/auth/wallet/verify",
        json={
            "wallet_address": account.address,
            "message": message,
            "signature": _sign(account, message),
        },
    )

    assert response.status_code == 429


# ---------------------------------------------------------------------------
# POST /auth/wallet/link/nonce, POST /auth/wallet/link
# ---------------------------------------------------------------------------


@patch("src.routes.wallet_auth.get_redis_client")
def test_link_nonce_uses_link_statement_and_per_user_key(mock_get_redis):
    redis_client = _redis()
    mock_get_redis.return_value = redis_client

    account = Account.create()
    response = client.post("/auth/wallet/link/nonce", json={"wallet_address": account.address})

    assert response.status_code == 200
    assert (
        f"Link this wallet to Gatewayz account {_TEST_USER_ID}."
        in response.json()["data"]["message"]
    )
    assert (
        redis_client.setex.call_args[0][0]
        == f"siwe_nonce:link:{_TEST_USER_ID}:{account.address.lower()}"
    )


@patch("src.routes.wallet_auth.link_wallet")
@patch("src.routes.wallet_auth.count_wallets")
@patch("src.routes.wallet_auth.get_wallet")
@patch("src.routes.wallet_auth.get_redis_client")
def test_link_first_wallet_becomes_primary(
    mock_get_redis, mock_get_wallet, mock_count_wallets, mock_link_wallet
):
    account = Account.create()
    message = _link_message(account.address, user_id=_TEST_USER_ID)
    mock_get_redis.return_value = _redis(getdel_return=message)
    mock_get_wallet.return_value = None
    mock_count_wallets.return_value = 0
    mock_link_wallet.return_value = {
        "wallet_address": account.address.lower(),
        "source": "siwe",
        "is_primary": True,
        "wallet_client_type": None,
        "verified_at": "2026-09-03T00:00:00Z",
    }

    response = client.post(
        "/auth/wallet/link",
        json={
            "wallet_address": account.address,
            "message": message,
            "signature": _sign(account, message),
        },
    )

    assert response.status_code == 200
    assert response.json()["data"]["wallet"]["is_primary"] is True
    _, kwargs = mock_link_wallet.call_args
    assert kwargs["make_primary"] is True


@patch("src.routes.wallet_auth.get_wallet")
@patch("src.routes.wallet_auth.get_redis_client")
def test_link_is_idempotent_when_already_linked_to_caller(mock_get_redis, mock_get_wallet):
    account = Account.create()
    message = _link_message(account.address, user_id=_TEST_USER_ID)
    mock_get_redis.return_value = _redis(getdel_return=message)
    mock_get_wallet.return_value = {
        "user_id": _TEST_USER_ID,
        "wallet_address": account.address.lower(),
        "source": "siwe",
        "is_primary": True,
        "wallet_client_type": None,
        "verified_at": "2026-09-03T00:00:00Z",
    }

    response = client.post(
        "/auth/wallet/link",
        json={
            "wallet_address": account.address,
            "message": message,
            "signature": _sign(account, message),
        },
    )

    assert response.status_code == 200


@patch("src.routes.wallet_auth.get_wallet")
@patch("src.routes.wallet_auth.get_redis_client")
def test_link_rejects_wallet_owned_by_another_user(mock_get_redis, mock_get_wallet):
    account = Account.create()
    message = _link_message(account.address, user_id=_TEST_USER_ID)
    mock_get_redis.return_value = _redis(getdel_return=message)
    mock_get_wallet.return_value = {"user_id": 999, "wallet_address": account.address.lower()}

    response = client.post(
        "/auth/wallet/link",
        json={
            "wallet_address": account.address,
            "message": message,
            "signature": _sign(account, message),
        },
    )

    assert response.status_code == 409
    # 409 detail text is masked by the global HTTPException handler
    # (pre-existing, app-wide) -- status code is the checked contract.


# ---------------------------------------------------------------------------
# GET /auth/wallets, DELETE /auth/wallets/{wallet_address}
# ---------------------------------------------------------------------------


@patch("src.routes.wallet_auth.get_wallets_for_user")
def test_list_wallets(mock_get_wallets):
    mock_get_wallets.return_value = [
        {
            "wallet_address": "0x" + "a" * 40,
            "source": "siwe",
            "is_primary": True,
            "wallet_client_type": None,
            "verified_at": "2026-09-03T00:00:00Z",
        }
    ]

    response = client.get("/auth/wallets")

    assert response.status_code == 200
    assert response.json()["data"]["wallets"][0]["wallet_address"] == "0x" + "a" * 40


@patch("src.routes.wallet_auth.unlink_wallet")
@patch("src.routes.wallet_auth.count_wallets")
@patch("src.routes.wallet_auth.users_module")
@patch("src.routes.wallet_auth.get_wallet")
def test_unlink_wallet_success(mock_get_wallet, mock_users_module, mock_count_wallets, mock_unlink):
    address = "0x" + "a" * 40
    mock_get_wallet.return_value = {"user_id": _TEST_USER_ID, "wallet_address": address}
    mock_users_module.get_user_by_id.return_value = {"id": _TEST_USER_ID, "auth_method": "privy"}
    mock_count_wallets.return_value = 2
    mock_unlink.return_value = True

    response = client.delete(f"/auth/wallets/{address}")

    assert response.status_code == 200


@patch("src.routes.wallet_auth.count_wallets")
@patch("src.routes.wallet_auth.users_module")
@patch("src.routes.wallet_auth.get_wallet")
def test_unlink_wallet_blocks_last_auth_method(
    mock_get_wallet, mock_users_module, mock_count_wallets
):
    address = "0x" + "a" * 40
    mock_get_wallet.return_value = {"user_id": _TEST_USER_ID, "wallet_address": address}
    mock_users_module.get_user_by_id.return_value = {"id": _TEST_USER_ID, "auth_method": "wallet"}
    mock_count_wallets.return_value = 1

    response = client.delete(f"/auth/wallets/{address}")

    assert response.status_code == 400
    assert response.json()["error"]["context"]["parameter_value"] == "last_auth_method"


@patch("src.routes.wallet_auth.get_wallet")
def test_unlink_wallet_404_when_not_linked_to_caller(mock_get_wallet):
    mock_get_wallet.return_value = None

    response = client.delete(f"/auth/wallets/{'0x' + 'a' * 40}")

    assert response.status_code == 404
