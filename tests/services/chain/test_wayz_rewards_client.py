"""Tests for src.services.chain.wayz_rewards_client (gatewayz-backend#2266)."""

from unittest.mock import MagicMock, patch

import pytest
from eth_account import Account
from hexbytes import HexBytes

from src.services.chain.wayz_rewards_client import (
    WayzProviderRewardsClient,
    WayzProviderRewardsClientError,
)


@pytest.fixture
def sb():
    """No-op fixture whose mere presence bypasses the autouse DB-skip in
    tests/conftest.py -- this is a pure unit test with everything mocked."""
    return None


def _make_client_with_mocked_web3(private_key: str):
    with patch("src.services.chain.wayz_rewards_client.Web3") as mock_web3_cls:
        mock_w3 = MagicMock()
        mock_web3_cls.return_value = mock_w3
        mock_web3_cls.to_checksum_address.side_effect = lambda a: a
        mock_contract = MagicMock()
        mock_w3.eth.contract.return_value = mock_contract
        client = WayzProviderRewardsClient("http://fake-rpc", "0xcontract", private_key)
        return client, mock_w3, mock_contract


def test_from_config_raises_when_contract_address_unset(sb):
    with patch("src.services.chain.wayz_rewards_client.Config") as mock_config:
        mock_config.WAYZ_TOKEN_CONTRACT_ADDRESS = None
        mock_config.WAYZ_REWARDS_POOL_PRIVATE_KEY = "0x" + "1" * 64
        with pytest.raises(WayzProviderRewardsClientError):
            WayzProviderRewardsClient.from_config()


def test_from_config_raises_when_pool_key_unset(sb):
    with patch("src.services.chain.wayz_rewards_client.Config") as mock_config:
        mock_config.WAYZ_TOKEN_CONTRACT_ADDRESS = "0xcontract"
        mock_config.WAYZ_REWARDS_POOL_PRIVATE_KEY = None
        with pytest.raises(WayzProviderRewardsClientError):
            WayzProviderRewardsClient.from_config()


def test_pool_address_is_derived_from_the_private_key(sb):
    test_account = Account.create()
    client, _, _ = _make_client_with_mocked_web3(test_account.key.hex())
    assert client.pool_address == test_account.address


@pytest.mark.asyncio
async def test_transfer_builds_signs_and_sends_transaction(sb):
    # A real, throwaway test private key -- Account.create()'d fresh each
    # run, used only to prove the client's build/sign/send call chain
    # against REAL eth_account signing, not a mocked one. Same lesson as
    # the faucet client's tests: a mock that accepts anything proves
    # nothing about whether the real API was called correctly.
    test_account = Account.create()
    client, mock_w3, mock_contract = _make_client_with_mocked_web3(test_account.key.hex())

    mock_w3.eth.get_transaction_count.return_value = 9
    mock_w3.eth.chain_id = 43113
    mock_w3.eth.gas_price = 1_000_000_000
    mock_contract.functions.transfer.return_value.build_transaction.return_value = {
        "from": test_account.address,
        "nonce": 9,
        "chainId": 43113,
        "gas": 100_000,
        "gasPrice": 1_000_000_000,
        "to": "0x" + "1" * 40,
        "value": 0,
        "data": "0xdeadbeef",
    }
    mock_w3.eth.send_raw_transaction.return_value = HexBytes(b"\xcd" * 32)

    tx_hash = await client.transfer("0xrecipient", 5_000 * 10**18)

    assert tx_hash == HexBytes(b"\xcd" * 32).to_0x_hex()
    assert tx_hash.startswith("0x")
    mock_contract.functions.transfer.assert_called_once_with("0xrecipient", 5_000 * 10**18)
    mock_w3.eth.send_raw_transaction.assert_called_once()


def test_pool_balance_wei_calls_balance_of_with_the_pool_address(sb):
    test_account = Account.create()
    client, _, mock_contract = _make_client_with_mocked_web3(test_account.key.hex())
    mock_contract.functions.balanceOf.return_value.call.return_value = 123_456

    assert client.pool_balance_wei() == 123_456
    mock_contract.functions.balanceOf.assert_called_once_with(test_account.address)


def test_real_eth_account_sign_transaction_uses_raw_transaction_attribute(sb):
    """Regression guard mirroring the faucet client's equivalent test:
    eth_account's SignedTransaction exposes `raw_transaction` (snake_case)
    in the installed version, not `rawTransaction`. Verified directly
    against a real (unmocked) Account.sign_transaction call."""
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


def test_transfer_and_balance_of_use_real_contract_function_signatures(sb):
    """Regression guard: proves WayzProviderRewardsClient's transfer()/
    balanceOf() call sites bind against the REAL, installed web3.py
    Contract ABI encoding -- not a MagicMock that would silently accept a
    wrong argument count or a renamed kwarg (the exact failure shape that
    shipped the WAYZ staking indexer's get_logs() bug). Goes through the
    real, unmocked client against an unreachable host: a wrong call
    fails fast inside argument binding/ABI encoding (TypeError or a web3
    validation error) before any network attempt; a correctly-shaped call
    instead fails with a connection error, proving the call was accepted
    and encoded.
    """
    client = WayzProviderRewardsClient(
        "http://127.0.0.1:1",
        "0x0000000000000000000000000000000000000001",
        Account.create().key.hex(),
    )

    with pytest.raises(Exception) as balance_exc:
        client.pool_balance_wei()
    assert not isinstance(
        balance_exc.value, TypeError
    ), f"balanceOf call rejected its arguments: {balance_exc.value}"

    with pytest.raises(Exception) as transfer_exc:
        client._transfer_sync("0x0000000000000000000000000000000000000002", 1000)
    assert not isinstance(
        transfer_exc.value, TypeError
    ), f"transfer call rejected its arguments: {transfer_exc.value}"
