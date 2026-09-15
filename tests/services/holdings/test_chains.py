"""Tests for src.services.holdings.chains (holdings rewards balance reader).

Every RPC call is mocked -- these tests must never touch a real node.
"""

from unittest.mock import MagicMock, patch

import pytest

from src.services.holdings.chains import (
    CHAIN_ID_BASE,
    CHAIN_ID_ETHEREUM,
    CHAIN_ID_POLYGON,
    BalanceReadResult,
    InvalidWalletAddressError,
    TokenRef,
    UnsupportedChainError,
    read_balances,
    rpc_url_for_chain,
)

WALLET = "0x742d35Cc6634C0532925a3b844Bc454e4438f44e"

ETH_NATIVE = TokenRef(
    chain_id=CHAIN_ID_ETHEREUM,
    contract_address=None,
    decimals=18,
    symbol="ETH",
    price_id="ethereum",
)
USDC_ETH = TokenRef(
    chain_id=CHAIN_ID_ETHEREUM,
    contract_address="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
    decimals=6,
    symbol="USDC",
    price_id="usd-coin",
)
MATIC_NATIVE = TokenRef(
    chain_id=CHAIN_ID_POLYGON,
    contract_address=None,
    decimals=18,
    symbol="POL",
    price_id="matic-network",
)
USDC_BASE = TokenRef(
    chain_id=CHAIN_ID_BASE,
    contract_address="0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
    decimals=6,
    symbol="USDC",
    price_id="usd-coin",
)


@pytest.fixture
def sb():
    """No-op fixture whose mere presence bypasses the autouse DB-skip in
    tests/conftest.py -- this is a pure unit test with everything mocked."""
    return None


def _fake_client(*, native_balance=0, erc20_balances=None, raises=None):
    """Build a MagicMock standing in for a web3.Web3 instance."""
    client = MagicMock()
    if raises is not None:
        client.eth.get_balance.side_effect = raises
        client.eth.contract.side_effect = raises
        return client

    client.eth.get_balance.return_value = native_balance
    balances = erc20_balances or {}

    def _contract(address, abi):  # noqa: ARG001 - abi unused in the stub
        contract = MagicMock()
        contract.functions.balanceOf.return_value.call.return_value = balances.get(address, 0)
        return contract

    client.eth.contract.side_effect = _contract
    return client


def _readings_by_symbol(result: BalanceReadResult) -> dict[str, int]:
    return {r.token.symbol: r.raw_amount for r in result.readings}


def test_rpc_url_for_chain_returns_configured_url(sb):
    assert rpc_url_for_chain(CHAIN_ID_ETHEREUM).startswith("http")


def test_rpc_url_for_chain_rejects_unsupported_chain(sb):
    with pytest.raises(UnsupportedChainError):
        rpc_url_for_chain(999999)


def test_invalid_wallet_address_raises_before_any_rpc_client_is_built(sb):
    with patch("src.services.holdings.chains._make_web3") as make_web3:
        with pytest.raises(InvalidWalletAddressError):
            read_balances("not-an-address", [ETH_NATIVE])
    make_web3.assert_not_called()


def test_empty_token_list_returns_empty_result_without_rpc(sb):
    with patch("src.services.holdings.chains._make_web3") as make_web3:
        result = read_balances(WALLET, [])

    assert result.readings == []
    assert result.failed_chain_ids == []
    assert result.is_complete is True
    make_web3.assert_not_called()


def test_tokens_are_grouped_one_client_per_chain(sb):
    clients = {
        CHAIN_ID_ETHEREUM: _fake_client(
            native_balance=5 * 10**18,
            erc20_balances={USDC_ETH.contract_address: 1_500_000},
        ),
        CHAIN_ID_POLYGON: _fake_client(native_balance=42 * 10**18),
    }
    calls: list[int] = []

    def _make(chain_id, rpc_url):  # noqa: ARG001
        calls.append(chain_id)
        return clients[chain_id]

    with patch("src.services.holdings.chains._make_web3", side_effect=_make):
        # Three tokens, two chains, and the Ethereum tokens are not adjacent --
        # grouping must still build exactly one client per chain.
        result = read_balances(WALLET, [ETH_NATIVE, MATIC_NATIVE, USDC_ETH])

    assert sorted(calls) == sorted([CHAIN_ID_ETHEREUM, CHAIN_ID_POLYGON])
    assert len(calls) == 2
    assert result.is_complete is True
    assert _readings_by_symbol(result) == {
        "ETH": 5 * 10**18,
        "USDC": 1_500_000,
        "POL": 42 * 10**18,
    }


def test_native_and_erc20_use_different_rpc_paths(sb):
    client = _fake_client(
        native_balance=7,
        erc20_balances={USDC_ETH.contract_address: 123},
    )
    with patch("src.services.holdings.chains._make_web3", return_value=client):
        result = read_balances(WALLET, [ETH_NATIVE, USDC_ETH])

    # Native balance comes from eth_getBalance, never from a contract call.
    client.eth.get_balance.assert_called_once_with(WALLET)
    assert client.eth.contract.call_count == 1
    assert _readings_by_symbol(result) == {"ETH": 7, "USDC": 123}


def test_raw_amounts_are_returned_undecoded(sb):
    """The reader returns raw integers; decimals live on the TokenRef so the
    caller (not this layer) decides how to scale them."""
    client = _fake_client(erc20_balances={USDC_ETH.contract_address: 1})
    with patch("src.services.holdings.chains._make_web3", return_value=client):
        result = read_balances(WALLET, [USDC_ETH])

    reading = result.readings[0]
    assert reading.raw_amount == 1
    assert isinstance(reading.raw_amount, int)
    assert reading.token.decimals == 6


def test_failing_chain_does_not_poison_other_chains(sb):
    clients = {
        CHAIN_ID_ETHEREUM: _fake_client(raises=TimeoutError("rpc timed out")),
        CHAIN_ID_POLYGON: _fake_client(native_balance=9),
        CHAIN_ID_BASE: _fake_client(erc20_balances={USDC_BASE.contract_address: 250_000}),
    }

    with patch(
        "src.services.holdings.chains._make_web3",
        side_effect=lambda chain_id, rpc_url: clients[chain_id],  # noqa: ARG005
    ):
        result = read_balances(WALLET, [ETH_NATIVE, USDC_ETH, MATIC_NATIVE, USDC_BASE])

    assert result.failed_chain_ids == [CHAIN_ID_ETHEREUM]
    assert result.is_complete is False
    # The healthy chains still produced their readings...
    assert _readings_by_symbol(result) == {"POL": 9, "USDC": 250_000}
    # ...and the failed chain contributed NO readings at all. A partial or
    # zero reading there would silently undervalue the wallet and cost the
    # user credits.
    assert all(r.token.chain_id != CHAIN_ID_ETHEREUM for r in result.readings)


def test_client_construction_failure_is_reported_as_a_failed_chain(sb):
    def _make(chain_id, rpc_url):  # noqa: ARG001
        if chain_id == CHAIN_ID_ETHEREUM:
            raise ConnectionError("bad endpoint")
        return _fake_client(native_balance=3)

    with patch("src.services.holdings.chains._make_web3", side_effect=_make):
        result = read_balances(WALLET, [ETH_NATIVE, MATIC_NATIVE])

    assert result.failed_chain_ids == [CHAIN_ID_ETHEREUM]
    assert _readings_by_symbol(result) == {"POL": 3}


def test_partial_chain_failure_discards_that_chains_earlier_readings(sb):
    """One token erroring marks the whole chain unread, including tokens that
    had already come back -- the caller can't tell a partial set from a
    complete one otherwise."""
    client = MagicMock()
    client.eth.get_balance.return_value = 5
    client.eth.contract.side_effect = TimeoutError("rpc timed out")

    with patch("src.services.holdings.chains._make_web3", return_value=client):
        result = read_balances(WALLET, [ETH_NATIVE, USDC_ETH])

    assert result.readings == []
    assert result.failed_chain_ids == [CHAIN_ID_ETHEREUM]
    assert result.is_complete is False


def test_unsupported_chain_in_token_list_is_reported_not_raised(sb):
    stray = TokenRef(
        chain_id=999999,
        contract_address=None,
        decimals=18,
        symbol="XXX",
        price_id="nope",
    )
    with patch(
        "src.services.holdings.chains._make_web3",
        return_value=_fake_client(native_balance=11),
    ):
        result = read_balances(WALLET, [MATIC_NATIVE, stray])

    assert result.failed_chain_ids == [999999]
    assert _readings_by_symbol(result) == {"POL": 11}


def test_failures_carry_a_reason_for_logging(sb):
    with patch(
        "src.services.holdings.chains._make_web3",
        return_value=_fake_client(raises=TimeoutError("rpc timed out")),
    ):
        result = read_balances(WALLET, [ETH_NATIVE])

    assert len(result.failures) == 1
    failure = result.failures[0]
    assert failure.chain_id == CHAIN_ID_ETHEREUM
    assert "rpc timed out" in failure.reason


def test_wallet_address_is_checksummed_before_use(sb):
    client = _fake_client(native_balance=1)
    with patch("src.services.holdings.chains._make_web3", return_value=client):
        read_balances(WALLET.lower(), [ETH_NATIVE])

    # EIP-55 checksum form, not the lowercase input.
    client.eth.get_balance.assert_called_once_with(WALLET)


def test_real_web3_client_accepts_our_call_shape(sb):
    """Regression guard against a mock that proves nothing.

    Every other test here mocks the web3 client, and a MagicMock accepts any
    method name and any keyword -- so it cannot catch an API mismatch with
    web3.py itself (this repo has been bitten exactly once already, by
    get_logs' from_block/fromBlock kwargs, with 25 green tests).

    This test builds a REAL Web3 against an unreachable host and goes through
    read_balances. Argument binding happens before any socket work, so a wrong
    provider kwarg or a wrong eth_getBalance signature would raise TypeError.
    Getting a connection failure instead proves our call shape is valid --
    and a connection failure is reported as a failed chain, not a zero.
    """
    unreachable = "http://127.0.0.1:1"
    with patch("src.services.holdings.chains.rpc_url_for_chain", return_value=unreachable):
        result = read_balances(WALLET, [ETH_NATIVE, USDC_ETH])

    assert result.failed_chain_ids == [CHAIN_ID_ETHEREUM]
    assert result.readings == []
    assert "TypeError" not in result.failures[0].reason
