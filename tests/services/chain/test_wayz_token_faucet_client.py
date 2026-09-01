"""Tests for src.services.chain.wayz_token_faucet_client (gatewayz-backend#2245)."""

from unittest.mock import MagicMock, patch

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from hexbytes import HexBytes

from src.services.chain.wayz_token_faucet_client import (
    WayzTokenFaucetClient,
    WayzTokenFaucetClientError,
)


@pytest.fixture
def sb():
    """No-op fixture whose mere presence bypasses the autouse DB-skip in
    tests/conftest.py -- this is a pure unit test with everything mocked."""
    return None


def _make_client_with_mocked_web3(private_key: str):
    with patch("src.services.chain.wayz_token_faucet_client.Web3") as mock_web3_cls:
        mock_w3 = MagicMock()
        mock_web3_cls.return_value = mock_w3
        mock_web3_cls.to_checksum_address.side_effect = lambda a: a
        mock_contract = MagicMock()
        mock_w3.eth.contract.return_value = mock_contract
        client = WayzTokenFaucetClient("http://fake-rpc", "0xcontract", private_key)
        return client, mock_w3, mock_contract


def test_from_config_raises_when_contract_address_unset(sb):
    with patch("src.services.chain.wayz_token_faucet_client.Config") as mock_config:
        mock_config.WAYZ_TOKEN_CONTRACT_ADDRESS = None
        mock_config.WAYZ_FAUCET_MINTER_PRIVATE_KEY = "0x" + "1" * 64
        with pytest.raises(WayzTokenFaucetClientError):
            WayzTokenFaucetClient.from_config()


def test_from_config_raises_when_minter_key_unset(sb):
    with patch("src.services.chain.wayz_token_faucet_client.Config") as mock_config:
        mock_config.WAYZ_TOKEN_CONTRACT_ADDRESS = "0xcontract"
        mock_config.WAYZ_FAUCET_MINTER_PRIVATE_KEY = None
        with pytest.raises(WayzTokenFaucetClientError):
            WayzTokenFaucetClient.from_config()


@pytest.mark.asyncio
async def test_mint_builds_signs_and_sends_transaction(sb):
    # A real, throwaway test private key -- not a secret, generated fresh
    # each run via Account.create(), used only to prove the client's
    # build/sign/send call chain against REAL eth_account signing (not a
    # mocked one) -- signing is the security-critical step to prove works
    # for real, matching the lesson from gatewayz-backend#2244's
    # get_logs bug (a fully-mocked call proves nothing about real usage).
    test_account = Account.create()
    client, mock_w3, mock_contract = _make_client_with_mocked_web3(test_account.key.hex())

    mock_w3.eth.get_transaction_count.return_value = 5
    mock_w3.eth.chain_id = 43113
    mock_w3.eth.gas_price = 1_000_000_000
    mock_contract.functions.mint.return_value.build_transaction.return_value = {
        "from": test_account.address,
        "nonce": 5,
        "chainId": 43113,
        "gas": 200_000,
        "gasPrice": 1_000_000_000,
        "to": "0x" + "1" * 40,
        "value": 0,
        "data": "0xdeadbeef",
    }
    mock_w3.eth.send_raw_transaction.return_value = MagicMock(
        hex=lambda: "0xabc123", to_0x_hex=lambda: "0xabc123"
    )

    tx_hash = await client.mint("0xrecipient", 1000)

    assert tx_hash == "0xabc123"
    mock_contract.functions.mint.assert_called_once_with("0xrecipient", 1000 * 10**18)
    mock_w3.eth.send_raw_transaction.assert_called_once()


@pytest.mark.asyncio
async def test_mint_returns_0x_prefixed_tx_hash(sb):
    """Regression guard: hexbytes==2.0.0's HexBytes.hex() no longer adds a
    '0x' prefix -- only to_0x_hex() does. A fully-mocked send_raw_transaction
    return value would hide this, so this test uses a REAL HexBytes (what
    web3's send_raw_transaction actually returns) to prove the prefix is
    present end to end."""
    test_account = Account.create()
    client, mock_w3, mock_contract = _make_client_with_mocked_web3(test_account.key.hex())

    mock_w3.eth.get_transaction_count.return_value = 5
    mock_w3.eth.chain_id = 43113
    mock_w3.eth.gas_price = 1_000_000_000
    mock_contract.functions.mint.return_value.build_transaction.return_value = {
        "from": test_account.address,
        "nonce": 5,
        "chainId": 43113,
        "gas": 200_000,
        "gasPrice": 1_000_000_000,
        "to": "0x" + "1" * 40,
        "value": 0,
        "data": "0xdeadbeef",
    }
    mock_w3.eth.send_raw_transaction.return_value = HexBytes(b"\xab" * 32)

    tx_hash = await client.mint("0xrecipient", 1000)

    assert tx_hash.startswith("0x")
    assert tx_hash == HexBytes(b"\xab" * 32).to_0x_hex()


def test_real_eth_account_sign_transaction_uses_raw_transaction_attribute(sb):
    """Regression guard: eth_account's SignedTransaction exposes
    `raw_transaction` (snake_case) in the installed version, not
    `rawTransaction`. Verified directly against a real (unmocked)
    Account.sign_transaction call -- if a future eth_account upgrade
    renames this attribute, this test catches it before the real
    mint() code path (which reads client._mint_sync's use of the same
    attribute) does."""
    account = Account.create()
    tx = {
        "to": "0x0000000000000000000000000000000000000001",
        "value": 0,
        "gas": 21000,
        "gasPrice": 1_000_000_000,
        "nonce": 0,
        "chainId": 43113,
    }
    signed = account.sign_transaction(tx)
    assert hasattr(signed, "raw_transaction")
    assert isinstance(signed.raw_transaction, (bytes, bytearray))
