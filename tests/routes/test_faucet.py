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
def test_claim_rejects_duplicate(mock_get_redis, mock_eligible, mock_existing, mock_client_cls):
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
def test_claim_returns_503_when_faucet_unconfigured(mock_get_redis, mock_create, mock_client_cls):
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
@patch("src.routes.faucet.create_pending_claim")
@patch("src.routes.faucet.get_redis_client")
def test_claim_returns_503_on_malformed_faucet_config(mock_get_redis, mock_create, mock_client_cls):
    """A malformed (not merely unset) config value -- e.g. a truncated
    minter private key -- fails inside WayzTokenFaucetClient.__init__,
    raising ValueError/binascii errors that are NOT
    WayzTokenFaucetClientError. Must still be a clean 503, not a raw 500."""
    mock_client_cls.from_config.side_effect = ValueError("Non-hexadecimal digit found")
    mock_redis = MagicMock()
    mock_get_redis.return_value = mock_redis

    account = Account.create()
    body = _signed_claim_body(42, "test-nonce-123", account)
    response = client.post("/faucet/claim", json=body)

    assert response.status_code == 503
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


@patch("src.routes.faucet.WayzTokenFaucetClient")
def test_claim_rejects_oversized_signature(mock_client_cls):
    mock_client_cls.from_config.return_value = MagicMock()
    account = Account.create()
    body = _signed_claim_body(42, "test-nonce-123", account)
    body["signature"] = "0x" + "ab" * 150  # well past a real 65-byte signature

    response = client.post("/faucet/claim", json=body)

    assert response.status_code == 422


@patch("src.routes.faucet.WayzTokenFaucetClient")
def test_status_unconfigured_returns_false(mock_client_cls):
    from src.services.chain.wayz_token_faucet_client import WayzTokenFaucetClientError

    mock_client_cls.from_config.side_effect = WayzTokenFaucetClientError("not configured")

    response = client.get("/faucet/status")

    assert response.status_code == 200
    assert response.json()["data"]["configured"] is False


@patch("src.routes.faucet.WayzTokenFaucetClient")
@patch("src.routes.faucet.get_claim_for_user")
@patch("src.routes.faucet.has_completed_at_least_one_request")
def test_status_eligible_with_existing_sent_claim(mock_eligible, mock_get_claim, mock_client_cls):
    mock_client_cls.from_config.return_value = MagicMock()
    mock_eligible.return_value = True
    mock_get_claim.return_value = {
        "status": "sent",
        "wallet_address": "0x" + "1" * 40,
        "tx_hash": "0xabc",
        "claimed_at": "2026-09-01T00:00:00+00:00",
        "error": "should never reach the client",
    }

    response = client.get("/faucet/status")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["eligible"] is True
    assert data["claim"]["status"] == "sent"
    assert "error" not in data["claim"]


@patch("src.routes.faucet.WayzTokenFaucetClient")
@patch("src.routes.faucet.get_claim_for_user")
@patch("src.routes.faucet.has_completed_at_least_one_request")
def test_status_with_wallet_address_scopes_to_caller(
    mock_eligible, mock_get_claim, mock_client_cls
):
    """GET /faucet/status must always look up the CALLER's own claim
    (get_claim_for_user, a plain user_id filter) -- never
    get_existing_claim's OR-across-wallet lookup, which is meant for the
    claim-write path's duplicate check and would leak another user's claim
    to anyone who supplies that user's wallet_address as a query param."""
    mock_client_cls.from_config.return_value = MagicMock()
    mock_eligible.return_value = True
    mock_get_claim.return_value = None

    wallet = "0x" + "4" * 40
    response = client.get(f"/faucet/status?wallet_address={wallet}")

    assert response.status_code == 200
    assert response.json()["data"]["claim"] is None
    mock_get_claim.assert_called_once_with(42)


@patch("src.routes.faucet.WayzTokenFaucetClient")
@patch("src.routes.faucet.get_claim_for_user")
@patch("src.routes.faucet.has_completed_at_least_one_request")
def test_status_wallet_address_mismatch_hides_callers_own_claim(
    mock_eligible, mock_get_claim, mock_client_cls
):
    """If the caller's own claim is for a DIFFERENT wallet than the one
    queried, don't surface it -- report no claim rather than leaking that
    some other wallet is linked to this user."""
    mock_client_cls.from_config.return_value = MagicMock()
    mock_eligible.return_value = True
    mock_get_claim.return_value = {
        "status": "sent",
        "wallet_address": "0x" + "5" * 40,
        "tx_hash": "0xabc",
        "claimed_at": "2026-09-01T00:00:00+00:00",
    }

    queried_wallet = "0x" + "6" * 40
    response = client.get(f"/faucet/status?wallet_address={queried_wallet}")

    assert response.status_code == 200
    assert response.json()["data"]["claim"] is None


@patch("src.routes.faucet.WayzTokenFaucetClient")
@patch("src.routes.faucet.get_existing_claim")
@patch("src.db.faucet.get_supabase_client")
@patch("src.routes.faucet.has_completed_at_least_one_request")
def test_status_does_not_leak_other_users_claim_by_wallet_probe(
    mock_eligible, mock_get_client, mock_get_existing, mock_client_cls
):
    """Regression for the IDOR found in review: user 999 (no claim of
    their own) probes user 5's already-claimed wallet_address via GET
    /faucet/status and must NOT receive user 5's claim. Exercises the
    real get_claim_for_user against a Supabase-client double that filters
    by the .eq("user_id", ...) call it actually makes -- not just a
    patched function -- and asserts get_existing_claim (the OR-across-
    wallet lookup responsible for the leak) is never even called."""
    mock_client_cls.from_config.return_value = MagicMock()
    mock_eligible.return_value = False

    victim_wallet = "0x" + "7" * 40
    victim_claim_row = {
        "id": 1,
        "user_id": 5,
        "wallet_address": victim_wallet,
        "status": "sent",
        "tx_hash": "0xdeadbeef",
        "claimed_at": "2026-08-01T00:00:00+00:00",
        "error": None,
    }

    table = MagicMock()
    table.select.return_value = table

    def _eq(column, value):
        assert column == "user_id"
        matches = [row for row in [victim_claim_row] if row["user_id"] == value]
        table.execute.return_value = MagicMock(data=matches)
        return table

    table.eq.side_effect = _eq
    client_mock = MagicMock()
    client_mock.table.return_value = table
    mock_get_client.return_value = client_mock

    app.dependency_overrides[get_user_id] = lambda: 999
    try:
        response = client.get(f"/faucet/status?wallet_address={victim_wallet}")
    finally:
        app.dependency_overrides[get_user_id] = lambda: 42

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["claim"] is None
    assert victim_claim_row["tx_hash"] not in str(data)
    mock_get_existing.assert_not_called()


def test_status_requires_auth():
    app.dependency_overrides.pop(get_user_id, None)
    try:
        response = client.get("/faucet/status")
        assert response.status_code == 401
    finally:
        app.dependency_overrides[get_user_id] = lambda: 42
