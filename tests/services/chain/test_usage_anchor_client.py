"""Tests for src.services.chain.usage_anchor_client -- the signing client for
UsageAnchor on Avalanche C-Chain. web3 is mocked; signing is real."""

from unittest.mock import MagicMock, patch

import pytest
from eth_account import Account
from web3 import Web3

from src.services.chain.usage_anchor_client import (
    USAGE_ANCHOR_ABI,
    AnchorBroadcastError,
    AnchorNotSentError,
    UsageAnchorClient,
    UsageAnchorClientError,
    anchorer_address_from_config,
    explorer_base,
    gas_limit_with_headroom,
)

CONTRACT = "0x00000000000000000000000000000000000000aa"
ROOT = b"\x42" * 32


@pytest.fixture
def sb():
    """No-op fixture whose presence bypasses the autouse DB-skip in
    tests/conftest.py -- pure unit tests, everything mocked."""
    return None


def _client(chain_id=43113, anchorer=None, onchain_chain_id=None):
    account = Account.create()
    client = UsageAnchorClient("http://127.0.0.1:1", CONTRACT, chain_id, account.key.hex())
    w3 = MagicMock()
    w3.eth.chain_id = onchain_chain_id if onchain_chain_id is not None else chain_id
    w3.eth.get_transaction_count.return_value = 7
    w3.eth.get_block.return_value = {"baseFeePerGas": 25_000_000_000}
    w3.eth.max_priority_fee = 1_000_000_000
    contract = MagicMock()
    contract.address = CONTRACT
    contract.functions.anchorer.return_value.call.return_value = anchorer or account.address
    call = contract.functions.anchor.return_value
    call.estimate_gas.return_value = 40_000

    def _build(params):
        return {
            **params,
            "to": Web3.to_checksum_address(CONTRACT),
            "data": "0xdeadbeef",
            "value": 0,
        }

    call.build_transaction.side_effect = _build
    client._w3 = w3
    client._contract = contract
    return client, w3, contract, account


def test_abi_has_the_fixed_interface(sb):
    names = {e["name"] for e in USAGE_ANCHOR_ABI}
    assert names == {"anchor", "rootOf", "leafCountOf", "anchorer", "Anchored"}
    anchor = next(e for e in USAGE_ANCHOR_ABI if e["name"] == "anchor")
    assert [i["type"] for i in anchor["inputs"]] == ["uint64", "bytes32", "uint64"]


def test_explorer_base_per_chain(sb):
    assert explorer_base(43114) == "https://snowtrace.io"
    assert explorer_base(43113) == "https://testnet.snowtrace.io"
    assert explorer_base(1) is None


def test_gas_headroom_rounds_up(sb):
    assert gas_limit_with_headroom(40_000) == 50_000
    assert gas_limit_with_headroom(3) == 4


def test_send_records_before_broadcast_and_signs_eip1559(sb):
    client, w3, contract, account = _client()
    order = []

    def record(signed):
        order.append(("record", signed.tx_hash))
        assert not w3.eth.send_raw_transaction.called
        return True

    w3.eth.send_raw_transaction.side_effect = lambda raw: order.append(("send", raw))
    info = client.send_anchor(20379, ROOT, 3, record)
    assert [o[0] for o in order] == ["record", "send"]
    assert info.nonce == 7 and info.gas == 50_000 and info.day == 20379
    contract.functions.anchor.assert_called_with(20379, ROOT, 3)
    params = contract.functions.anchor.return_value.build_transaction.call_args.args[0]
    assert params["chainId"] == 43113 and params["type"] == 2
    assert params["maxFeePerGas"] == 2 * 25_000_000_000 + 1_000_000_000
    assert params["from"] == account.address
    w3.eth.get_transaction_count.assert_called_with(account.address, "pending")


def test_record_false_aborts_without_sending(sb):
    client, w3, _, _ = _client()
    with pytest.raises(AnchorNotSentError):
        client.send_anchor(1, ROOT, 1, lambda s: False)
    assert not w3.eth.send_raw_transaction.called


def test_broadcast_failure_is_uncertain_not_unsent(sb):
    client, w3, _, _ = _client()
    w3.eth.send_raw_transaction.side_effect = RuntimeError("connection reset")
    with pytest.raises(AnchorBroadcastError) as exc:
        client.send_anchor(1, ROOT, 1, lambda s: True)
    assert exc.value.tx_hash.startswith("0x") and exc.value.nonce == 7


def test_wrong_chain_refuses_before_signing(sb):
    client, w3, _, _ = _client(chain_id=43114, onchain_chain_id=43113)
    with pytest.raises(AnchorNotSentError, match="chain id"):
        client.send_anchor(1, ROOT, 1, lambda s: True)
    assert not w3.eth.send_raw_transaction.called


def test_signer_not_anchorer_refuses(sb):
    client, w3, _, _ = _client(anchorer="0x00000000000000000000000000000000000000bb")
    with pytest.raises(AnchorNotSentError, match="not the contract's anchorer"):
        client.send_anchor(1, ROOT, 1, lambda s: True)


def test_estimate_revert_is_unsent(sb):
    client, w3, contract, _ = _client()
    contract.functions.anchor.return_value.estimate_gas.side_effect = RuntimeError(
        "execution reverted: AlreadyAnchored"
    )
    with pytest.raises(AnchorNotSentError, match="AlreadyAnchored"):
        client.send_anchor(1, ROOT, 1, lambda s: True)


@pytest.mark.parametrize("root,count", [(b"\x00" * 32, 1), (b"\x01" * 31, 1), (ROOT, 0)])
def test_invalid_root_or_count_refused_locally(sb, root, count):
    client, w3, _, _ = _client()
    with pytest.raises(AnchorNotSentError):
        client.send_anchor(1, root, count, lambda s: True)
    assert not w3.eth.get_transaction_count.called


def test_root_of_maps_zero_to_none(sb):
    client, _, contract, _ = _client()
    contract.functions.rootOf.return_value.call.return_value = b"\x00" * 32
    assert client.root_of(5) is None
    contract.functions.rootOf.return_value.call.return_value = ROOT
    assert client.root_of(5) == ROOT


def test_from_config_requires_contract_and_key(sb):
    with patch("src.services.chain.usage_anchor_client.Config") as cfg:
        cfg.USAGE_ANCHOR_CONTRACT_ADDRESS = None
        with pytest.raises(UsageAnchorClientError, match="CONTRACT"):
            UsageAnchorClient.from_config()
        cfg.USAGE_ANCHOR_CONTRACT_ADDRESS = CONTRACT
        cfg.USAGE_ANCHOR_PRIVATE_KEY = None
        with pytest.raises(UsageAnchorClientError, match="PRIVATE_KEY"):
            UsageAnchorClient.from_config()


def test_from_config_refuses_the_payout_key(sb):
    key = Account.create().key.hex()
    with patch("src.services.chain.usage_anchor_client.Config") as cfg:
        cfg.USAGE_ANCHOR_CONTRACT_ADDRESS = CONTRACT
        cfg.USAGE_ANCHOR_PRIVATE_KEY = key
        cfg.PROVIDER_PAYOUT_POOL_PRIVATE_KEY = key
        with pytest.raises(UsageAnchorClientError, match="dedicated"):
            UsageAnchorClient.from_config()


def test_malformed_key_error_never_echoes_the_key(sb):
    with patch("src.services.chain.usage_anchor_client.Config") as cfg:
        cfg.USAGE_ANCHOR_CONTRACT_ADDRESS = CONTRACT
        cfg.USAGE_ANCHOR_PRIVATE_KEY = "0xnot-a-real-key-SECRET"
        cfg.PROVIDER_PAYOUT_POOL_PRIVATE_KEY = None
        cfg.USAGE_ANCHOR_RPC_URL = "http://127.0.0.1:1"
        cfg.USAGE_ANCHOR_CHAIN_ID = 43113
        with pytest.raises(UsageAnchorClientError) as exc:
            UsageAnchorClient.from_config()
        assert "SECRET" not in str(exc.value)


def test_anchorer_address_from_config(sb):
    account = Account.create()
    with patch("src.services.chain.usage_anchor_client.Config") as cfg:
        cfg.USAGE_ANCHOR_PRIVATE_KEY = account.key.hex()
        assert anchorer_address_from_config() == account.address
        cfg.USAGE_ANCHOR_PRIVATE_KEY = None
        assert anchorer_address_from_config() is None
        cfg.USAGE_ANCHOR_PRIVATE_KEY = "garbage"
        assert anchorer_address_from_config() is None
