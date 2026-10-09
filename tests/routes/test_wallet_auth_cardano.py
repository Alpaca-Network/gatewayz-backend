"""Tests for src/routes/wallet_auth_cardano.py -- linking a Cardano stake
address with a CIP-30 signData proof. Proofs are signed with a real Ed25519
key; Redis and user_wallets are mocked."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

import src.routes.wallet_auth_cardano as cardano_routes
from src.main import app
from src.security.deps import get_user_id
from src.services.delegation import cip8
from tests.services.delegation.test_cip8 import _cose_key_hex, _raw_public, _sign1_hex

client = TestClient(app)
USER_ID = 42


@pytest.fixture(autouse=True)
def _overrides():
    saved = dict(app.dependency_overrides)
    app.dependency_overrides[get_user_id] = lambda: USER_ID
    app.dependency_overrides[cardano_routes.cardano_link_rl] = lambda: None
    app.dependency_overrides[cardano_routes.cardano_link_nonce_rl] = lambda: None
    yield
    app.dependency_overrides.clear()
    app.dependency_overrides.update(saved)


@pytest.fixture
def wallet():
    private = Ed25519PrivateKey.generate()
    public = _raw_public(private)
    address = cip8.stake_address_from_public_key(public)
    return private, public, address, cip8.parse_stake_address(address)


def _redis(stored=None):
    redis_client = MagicMock()
    redis_client.getdel.return_value = stored
    return redis_client


def _proof(wallet, message):
    private, public, address, raw = wallet
    return {
        "stake_address": address,
        "signature": _sign1_hex(private, raw, message.encode()),
        "key": _cose_key_hex(public),
    }


def test_nonce_stores_and_returns_the_exact_message(wallet):
    redis_client = _redis()
    with patch.object(cardano_routes, "get_redis_client", return_value=redis_client):
        response = client.post("/auth/wallet/cardano/nonce", json={"stake_address": wallet[2]})
    assert response.status_code == 200
    data = response.json()["data"]
    key, ttl, stored = redis_client.setex.call_args.args
    assert key == f"cip30_nonce:link:{USER_ID}:{wallet[2]}"
    assert ttl == 300 and stored == data["message"]
    assert bytes.fromhex(data["payload_hex"]).decode() == data["message"]
    assert data["nonce"] in data["message"] and wallet[2] in data["message"]
    assert f"account {USER_ID}" in data["message"]
    assert set(data) == {"nonce", "message", "payload_hex", "expires_at"}


def test_nonce_rejects_testnet_and_payment_addresses():
    testnet = cip8.bech32_encode("stake_test", bytes([0xE0]) + b"\x01" * 28)
    payment = cip8.bech32_encode("stake", bytes([0x61]) + b"\x01" * 28)
    with patch.object(cardano_routes, "get_redis_client", return_value=_redis()):
        for address in (testnet, payment):
            response = client.post("/auth/wallet/cardano/nonce", json={"stake_address": address})
            assert response.status_code == 422


def test_valid_proof_links_as_cip34_and_pays_pending(wallet):
    message = cardano_routes.build_cardano_link_message(
        USER_ID, wallet[2], "n1", cardano_routes.datetime.now(cardano_routes.UTC)
    )
    row = {"wallet_address": wallet[2], "chain_namespace": "cip34", "source": "cip30"}
    with (
        patch.object(cardano_routes, "get_redis_client", return_value=_redis(message)),
        patch.object(cardano_routes, "get_wallet", return_value=None),
        patch.object(cardano_routes, "link_wallet", return_value=row) as link,
        patch.object(cardano_routes, "_pay_pending_delegation_rewards") as pay,
    ):
        response = client.post("/auth/wallet/cardano/link", json=_proof(wallet, message))
    assert response.status_code == 200
    assert response.json()["data"]["wallet"]["chain_namespace"] == "cip34"
    args, kwargs = link.call_args
    assert args == (USER_ID, wallet[2])
    assert kwargs == {"source": "cip30", "make_primary": False, "chain_namespace": "cip34"}
    pay.assert_called_once_with(wallet[2], USER_ID)


def test_replayed_or_expired_nonce_is_rejected(wallet):
    with patch.object(cardano_routes, "get_redis_client", return_value=_redis(None)):
        response = client.post("/auth/wallet/cardano/link", json=_proof(wallet, "anything"))
    assert response.status_code == 400


def test_signature_over_a_different_message_is_rejected(wallet):
    with (
        patch.object(cardano_routes, "get_redis_client", return_value=_redis("the issued one")),
        patch.object(cardano_routes, "link_wallet") as link,
    ):
        response = client.post("/auth/wallet/cardano/link", json=_proof(wallet, "another one"))
    assert response.status_code == 401
    link.assert_not_called()


def test_someone_elses_address_with_my_key_is_rejected(wallet):
    message = "issued"
    attacker = Ed25519PrivateKey.generate()
    body = {
        "stake_address": wallet[2],
        "signature": _sign1_hex(attacker, wallet[3], message.encode()),
        "key": _cose_key_hex(_raw_public(attacker)),
    }
    with (
        patch.object(cardano_routes, "get_redis_client", return_value=_redis(message)),
        patch.object(cardano_routes, "link_wallet") as link,
    ):
        response = client.post("/auth/wallet/cardano/link", json=body)
    assert response.status_code == 401
    link.assert_not_called()


def test_address_linked_to_another_account_is_409(wallet):
    message = "issued"
    with (
        patch.object(cardano_routes, "get_redis_client", return_value=_redis(message)),
        patch.object(cardano_routes, "get_wallet", return_value={"user_id": 7}),
        patch.object(cardano_routes, "link_wallet") as link,
    ):
        response = client.post("/auth/wallet/cardano/link", json=_proof(wallet, message))
    assert response.status_code == 409
    link.assert_not_called()


def test_requires_a_caller():
    app.dependency_overrides.pop(get_user_id, None)
    response = client.post("/auth/wallet/cardano/nonce", json={"stake_address": "stake1x"})
    assert response.status_code in (401, 403, 422)


def test_unlink_accepts_a_stake_address(wallet):
    import src.routes.wallet_auth as wallet_auth

    with (
        patch.object(wallet_auth, "get_wallet", return_value={"user_id": USER_ID}),
        patch.object(
            wallet_auth.users_module, "get_user_by_id", return_value={"auth_method": "email"}
        ),
        patch.object(wallet_auth, "unlink_wallet", return_value=True) as unlink,
    ):
        response = client.delete(f"/auth/wallets/{wallet[2].upper()}")
    assert response.status_code == 200
    unlink.assert_called_once_with(USER_ID, wallet[2])
