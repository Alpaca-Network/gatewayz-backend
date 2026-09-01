"""Tests for src.routes.faucet (gatewayz-backend#2245)."""

from unittest.mock import MagicMock, patch

from eth_account import Account
from eth_account.messages import encode_defunct
from fastapi.testclient import TestClient

from src.main import app
from src.security.deps import get_user_id

client = TestClient(app)
app.dependency_overrides[get_user_id] = lambda: 42


def _signed_claim_body(user_id: int, nonce: str, account) -> dict:
    message = f"Claim testnet WAYZ for Gatewayz account {user_id}. Nonce: {nonce}."
    signature = account.sign_message(encode_defunct(text=message)).signature.hex()
    return {"wallet_address": account.address, "signature": f"0x{signature}"}


@patch("src.routes.faucet.get_redis_client")
def test_nonce_endpoint_returns_a_nonce(mock_get_redis):
    mock_redis = MagicMock()
    mock_get_redis.return_value = mock_redis

    response = client.post("/faucet/nonce", json={"wallet_address": "0x" + "1" * 40})

    assert response.status_code == 200
    assert "message" in response.json()["data"]
    mock_redis.setex.assert_called_once()


@patch("src.routes.faucet.get_redis_client")
def test_nonce_endpoint_normalizes_uppercase_wallet_address(mock_get_redis):
    mock_redis = MagicMock()
    mock_get_redis.return_value = mock_redis

    response = client.post("/faucet/nonce", json={"wallet_address": "0x" + "A" * 40})

    assert response.status_code == 200
    args, _ = mock_redis.setex.call_args
    assert args[0] == f"faucet_nonce:42:0x{'a' * 40}"


@patch("src.routes.faucet.get_redis_client")
def test_nonce_endpoint_rejects_invalid_wallet_address(mock_get_redis):
    mock_redis = MagicMock()
    mock_get_redis.return_value = mock_redis

    response = client.post("/faucet/nonce", json={"wallet_address": "not-an-address"})

    assert response.status_code == 422


@patch("src.routes.faucet.WayzTokenFaucetClient")
@patch("src.routes.faucet.create_pending_claim")
@patch("src.routes.faucet.get_existing_claim")
@patch("src.routes.faucet.has_completed_at_least_one_request")
@patch("src.routes.faucet.get_redis_client")
def test_claim_succeeds_with_valid_signature(
    mock_get_redis, mock_eligible, mock_existing, mock_create, mock_client_cls
):
    account = Account.create()
    nonce = "test-nonce-123"
    mock_redis = MagicMock()
    mock_redis.getdel.return_value = nonce
    mock_get_redis.return_value = mock_redis
    mock_eligible.return_value = True
    mock_existing.return_value = None
    mock_create.return_value = {"id": 7, "user_id": 42, "wallet_address": account.address}

    mock_client_instance = MagicMock()

    async def _mint(*args, **kwargs):
        return "0xtxhash"

    mock_client_instance.mint = _mint
    mock_client_cls.from_config.return_value = mock_client_instance

    body = _signed_claim_body(42, nonce, account)
    response = client.post("/faucet/claim", json=body)

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["tx_hash"] == "0xtxhash"
    mock_redis.getdel.assert_called_once()
    create_args, _ = mock_create.call_args
    assert create_args[2] == 1000 * 10**18


@patch("src.routes.faucet.WayzTokenFaucetClient")
@patch("src.routes.faucet.get_redis_client")
def test_claim_rejects_missing_nonce(mock_get_redis, mock_client_cls):
    mock_client_cls.from_config.return_value = MagicMock()
    mock_redis = MagicMock()
    mock_redis.getdel.return_value = None
    mock_get_redis.return_value = mock_redis

    account = Account.create()
    body = _signed_claim_body(42, "whatever-nonce", account)
    response = client.post("/faucet/claim", json=body)

    assert response.status_code == 400


@patch("src.routes.faucet.WayzTokenFaucetClient")
@patch("src.routes.faucet.get_redis_client")
def test_claim_rejects_wrong_signer(mock_get_redis, mock_client_cls):
    mock_client_cls.from_config.return_value = MagicMock()
    nonce = "test-nonce-123"
    mock_redis = MagicMock()
    mock_redis.getdel.return_value = nonce
    mock_get_redis.return_value = mock_redis

    signer_account = Account.create()
    claimed_wallet = Account.create()  # different address than the actual signer
    body = _signed_claim_body(42, nonce, signer_account)
    body["wallet_address"] = claimed_wallet.address  # mismatch

    response = client.post("/faucet/claim", json=body)

    assert response.status_code == 401


@patch("src.routes.faucet.WayzTokenFaucetClient")
@patch("src.routes.faucet.get_existing_claim")
@patch("src.routes.faucet.has_completed_at_least_one_request")
@patch("src.routes.faucet.get_redis_client")
def test_claim_rejects_ineligible_account(
    mock_get_redis, mock_eligible, mock_existing, mock_client_cls
):
    mock_client_cls.from_config.return_value = MagicMock()
    account = Account.create()
    nonce = "test-nonce-123"
    mock_redis = MagicMock()
    mock_redis.getdel.return_value = nonce
    mock_get_redis.return_value = mock_redis
    mock_eligible.return_value = False

    body = _signed_claim_body(42, nonce, account)
    response = client.post("/faucet/claim", json=body)

    assert response.status_code == 403


@patch("src.routes.faucet.WayzTokenFaucetClient")
@patch("src.routes.faucet.get_existing_claim")
@patch("src.routes.faucet.has_completed_at_least_one_request")
@patch("src.routes.faucet.get_redis_client")
def test_claim_rejects_duplicate(
    mock_get_redis, mock_eligible, mock_existing, mock_client_cls
):
    mock_client_cls.from_config.return_value = MagicMock()
    account = Account.create()
    nonce = "test-nonce-123"
    mock_redis = MagicMock()
    mock_redis.getdel.return_value = nonce
    mock_get_redis.return_value = mock_redis
    mock_eligible.return_value = True
    mock_existing.return_value = {"id": 1, "status": "sent"}

    body = _signed_claim_body(42, nonce, account)
    response = client.post("/faucet/claim", json=body)

    assert response.status_code == 409


@patch("src.routes.faucet.WayzTokenFaucetClient")
@patch("src.routes.faucet.create_pending_claim")
@patch("src.routes.faucet.get_redis_client")
def test_claim_returns_503_when_faucet_unconfigured(
    mock_get_redis, mock_create, mock_client_cls
):
    from src.services.chain.wayz_token_faucet_client import WayzTokenFaucetClientError

    mock_client_cls.from_config.side_effect = WayzTokenFaucetClientError("not configured")
    mock_redis = MagicMock()
    mock_get_redis.return_value = mock_redis

    account = Account.create()
    body = _signed_claim_body(42, "test-nonce-123", account)
    response = client.post("/faucet/claim", json=body)

    assert response.status_code == 503
    # The faucet-configured check now runs before any DB writes, so a
    # pending claim row is never created -- and never gets permanently
    # burned by an unconfigured faucet.
    mock_create.assert_not_called()
    mock_redis.getdel.assert_not_called()


@patch("src.routes.faucet.WayzTokenFaucetClient")
def test_claim_rejects_invalid_wallet_address(mock_client_cls):
    mock_client_cls.from_config.return_value = MagicMock()
    account = Account.create()
    body = _signed_claim_body(42, "test-nonce-123", account)
    body["wallet_address"] = "not-an-address"

    response = client.post("/faucet/claim", json=body)

    assert response.status_code == 422
