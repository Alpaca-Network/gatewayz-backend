"""Tests for src.services.holdings.chains (holdings rewards balance reader).

Every RPC call is mocked -- these tests must never touch a real node.
"""

import logging
import os
from unittest.mock import MagicMock, patch

import pytest
import requests
from web3.exceptions import ContractLogicError, Web3RPCError

import src.services.holdings.chains as chains
from src.config.config import Config
from src.services.holdings.chains import (
    CHAIN_ID_ARBITRUM_ONE,
    CHAIN_ID_AVALANCHE_C_CHAIN,
    CHAIN_ID_BASE,
    CHAIN_ID_BNB_CHAIN,
    CHAIN_ID_ETHEREUM,
    CHAIN_ID_POLYGON,
    SUPPORTED_CHAIN_IDS,
    BalanceReadResult,
    InvalidWalletAddressError,
    RpcEndpoint,
    TokenRef,
    UnsupportedChainError,
    is_transport_error,
    read_balances,
    redact_rpc_error,
    rpc_endpoints_for_chain,
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


@pytest.fixture(autouse=True)
def _fresh_endpoint_health():
    """Endpoint demotion is module-level state; never let one test's failed
    endpoint reorder another test's endpoints."""
    chains.reset_endpoint_health()
    yield
    chains.reset_endpoint_health()


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
    unreachable = [RpcEndpoint(url="http://127.0.0.1:1", source="public")]
    with patch("src.services.holdings.chains.rpc_endpoints_for_chain", return_value=unreachable):
        result = read_balances(WALLET, [ETH_NATIVE, USDC_ETH])

    assert result.failed_chain_ids == [CHAIN_ID_ETHEREUM]
    assert result.readings == []
    assert "TypeError" not in result.failures[0].reason


# ---------------------------------------------------------------------------
# RPC endpoint precedence: explicit env > Alchemy > public default, then
# public fallbacks.
# ---------------------------------------------------------------------------

ALCHEMY_KEY = "alch-test-key-0123456789"


def _public(chain_id):
    return chains._PUBLIC_RPC_URLS[chain_id]


class TestEndpointPrecedence:
    def test_config_defaults_match_the_public_defaults(self, sb):
        """The public default is how an explicit-but-default value is told
        apart from a real override, so it must not drift from Config."""
        for chain_id, attr in chains._RPC_CONFIG_ATTR.items():
            if os.environ.get(attr):
                continue  # an operator override in this shell; nothing to compare
            assert getattr(Config, attr) == _public(chain_id)[0], attr

    def test_no_override_and_no_key_reads_public_then_secondary(self, sb, monkeypatch):
        monkeypatch.setattr(Config, "ALCHEMY_API_KEY", None)
        monkeypatch.setattr(Config, "POLYGON_RPC_URL", _public(CHAIN_ID_POLYGON)[0])
        endpoints = rpc_endpoints_for_chain(CHAIN_ID_POLYGON)
        assert [e.url for e in endpoints] == list(_public(CHAIN_ID_POLYGON))
        assert {e.source for e in endpoints} == {"public"}

    def test_alchemy_key_beats_the_public_default(self, sb, monkeypatch):
        monkeypatch.setattr(Config, "ALCHEMY_API_KEY", ALCHEMY_KEY)
        monkeypatch.setattr(Config, "ETHEREUM_RPC_URL", _public(CHAIN_ID_ETHEREUM)[0])
        endpoints = rpc_endpoints_for_chain(CHAIN_ID_ETHEREUM)
        assert endpoints[0] == RpcEndpoint(
            url=f"https://eth-mainnet.g.alchemy.com/v2/{ALCHEMY_KEY}", source="alchemy"
        )
        # The public endpoints stay behind it as fallbacks.
        assert [e.url for e in endpoints[1:]] == list(_public(CHAIN_ID_ETHEREUM))

    def test_explicit_override_beats_alchemy(self, sb, monkeypatch):
        monkeypatch.setattr(Config, "ALCHEMY_API_KEY", ALCHEMY_KEY)
        monkeypatch.setattr(Config, "BASE_RPC_URL", "https://paid.example/base")
        endpoints = rpc_endpoints_for_chain(CHAIN_ID_BASE)
        assert [e.source for e in endpoints] == ["env", "alchemy", "public", "public"]
        assert endpoints[0].url == "https://paid.example/base"
        assert rpc_url_for_chain(CHAIN_ID_BASE) == "https://paid.example/base"

    def test_override_equal_to_the_public_default_is_treated_as_unset(self, sb, monkeypatch):
        """Prod sets POLYGON_RPC_URL to the publicnode default; adding
        ALCHEMY_API_KEY must still upgrade Polygon to Alchemy."""
        monkeypatch.setattr(Config, "ALCHEMY_API_KEY", ALCHEMY_KEY)
        monkeypatch.setattr(Config, "POLYGON_RPC_URL", _public(CHAIN_ID_POLYGON)[0])
        endpoints = rpc_endpoints_for_chain(CHAIN_ID_POLYGON)
        assert endpoints[0].source == "alchemy"
        assert len({e.url for e in endpoints}) == len(endpoints)  # no duplicates

    @pytest.mark.parametrize(
        ("chain_id", "slug"),
        [
            (CHAIN_ID_ETHEREUM, "eth-mainnet"),
            (CHAIN_ID_BASE, "base-mainnet"),
            (CHAIN_ID_ARBITRUM_ONE, "arb-mainnet"),
            (CHAIN_ID_POLYGON, "polygon-mainnet"),
            (CHAIN_ID_BNB_CHAIN, "bnb-mainnet"),
            (CHAIN_ID_AVALANCHE_C_CHAIN, "avax-mainnet"),
        ],
    )
    def test_alchemy_network_slug_per_chain(self, sb, monkeypatch, chain_id, slug):
        monkeypatch.setattr(Config, "ALCHEMY_API_KEY", ALCHEMY_KEY)
        monkeypatch.setattr(Config, chains._RPC_CONFIG_ATTR[chain_id], None)
        assert rpc_url_for_chain(chain_id) == f"https://{slug}.g.alchemy.com/v2/{ALCHEMY_KEY}"

    def test_every_supported_chain_has_a_public_default_and_alchemy_slug(self, sb):
        for chain_id in SUPPORTED_CHAIN_IDS:
            default, secondary = _public(chain_id)
            assert default.startswith("https://") and secondary.startswith("https://")
            assert default != secondary
            assert chain_id in chains._ALCHEMY_NETWORKS

    def test_endpoint_repr_never_shows_the_url(self, sb):
        endpoint = RpcEndpoint(url=f"https://x.g.alchemy.com/v2/{ALCHEMY_KEY}", source="alchemy")
        assert ALCHEMY_KEY not in repr(endpoint)


# ---------------------------------------------------------------------------
# Fallback: transport failures move to the next endpoint, reverts never do.
# ---------------------------------------------------------------------------

PRIMARY = "https://primary.example/rpc"
FALLBACK = "https://fallback.example/rpc"


def _two_endpoints(chain_id):  # noqa: ARG001
    return [RpcEndpoint(url=PRIMARY, source="alchemy"), RpcEndpoint(url=FALLBACK, source="public")]


def _http_401():
    response = requests.Response()
    response.status_code = 401
    response.url = f"https://eth-mainnet.g.alchemy.com/v2/{ALCHEMY_KEY}"
    return requests.HTTPError(
        f"401 Client Error: Unauthorized for url: {response.url}", response=response
    )


def _read_with(clients_by_url, tokens):
    calls: list[str] = []

    def _make(chain_id, rpc_url):  # noqa: ARG001
        calls.append(rpc_url)
        return clients_by_url[rpc_url]

    with (
        patch("src.services.holdings.chains.rpc_endpoints_for_chain", side_effect=_two_endpoints),
        patch("src.services.holdings.chains._make_web3", side_effect=_make),
    ):
        result = read_balances(WALLET, tokens)
    return result, calls


class TestFallback:
    @pytest.mark.parametrize(
        "error",
        [
            requests.ConnectionError("connection refused"),
            requests.Timeout("read timed out"),
            TimeoutError("rpc timed out"),
            _http_401(),
            Web3RPCError("limit exceeded", rpc_response={"error": {"code": -32005}}),
        ],
        ids=["connection", "requests-timeout", "timeout", "http-401", "rate-limit-code"],
    )
    def test_transport_failure_is_retried_on_the_fallback(self, sb, error):
        clients = {
            PRIMARY: _fake_client(raises=error),
            FALLBACK: _fake_client(native_balance=4, erc20_balances={USDC_ETH.contract_address: 9}),
        }
        result, calls = _read_with(clients, [ETH_NATIVE, USDC_ETH])

        assert calls == [PRIMARY, FALLBACK]
        assert result.is_complete is True
        assert _readings_by_symbol(result) == {"ETH": 4, "USDC": 9}

    def test_a_revert_is_never_retried_elsewhere(self, sb):
        """A revert is a property of the call, not the endpoint: it would
        revert identically on the fallback. Counting it as an endpoint
        failure is the Alchemy-failover mistake (see the vault note)."""
        clients = {
            PRIMARY: _fake_client(raises=ContractLogicError("execution reverted")),
            FALLBACK: _fake_client(native_balance=4),
        }
        result, calls = _read_with(clients, [USDC_ETH])

        assert calls == [PRIMARY]
        assert result.failed_chain_ids == [CHAIN_ID_ETHEREUM]
        assert result.readings == []

    def test_an_unrecognised_rpc_error_is_not_treated_as_transport(self, sb):
        error = Web3RPCError("execution reverted", rpc_response={"error": {"code": 3}})
        clients = {PRIMARY: _fake_client(raises=error), FALLBACK: _fake_client(native_balance=1)}
        result, calls = _read_with(clients, [ETH_NATIVE])

        assert calls == [PRIMARY]
        assert result.failed_chain_ids == [CHAIN_ID_ETHEREUM]

    def test_every_endpoint_failing_is_still_a_failed_chain_not_a_zero(self, sb):
        clients = {
            PRIMARY: _fake_client(raises=requests.ConnectionError("down")),
            FALLBACK: _fake_client(raises=requests.ConnectionError("also down")),
        }
        result, calls = _read_with(clients, [ETH_NATIVE, USDC_ETH])

        assert calls == [PRIMARY, FALLBACK]
        assert result.failed_chain_ids == [CHAIN_ID_ETHEREUM]
        assert result.readings == []

    def test_a_mid_chain_transport_failure_rereads_the_whole_chain(self, sb):
        primary = MagicMock()
        primary.eth.get_balance.return_value = 999  # must NOT leak into the result
        primary.eth.contract.side_effect = requests.ConnectionError("dropped")
        clients = {
            PRIMARY: primary,
            FALLBACK: _fake_client(native_balance=5, erc20_balances={USDC_ETH.contract_address: 6}),
        }
        result, _calls = _read_with(clients, [ETH_NATIVE, USDC_ETH])

        assert _readings_by_symbol(result) == {"ETH": 5, "USDC": 6}

    def test_a_failed_endpoint_is_tried_last_for_a_while(self, sb, monkeypatch):
        monkeypatch.setattr(Config, "ALCHEMY_API_KEY", ALCHEMY_KEY)
        monkeypatch.setattr(Config, "ETHEREUM_RPC_URL", "https://paid.example/eth")
        first = rpc_endpoints_for_chain(CHAIN_ID_ETHEREUM)
        chains._demote(first[0])

        reordered = rpc_endpoints_for_chain(CHAIN_ID_ETHEREUM)
        assert reordered[-1] == first[0]  # demoted, not dropped
        assert reordered[:-1] == first[1:]

        chains.reset_endpoint_health()
        assert rpc_endpoints_for_chain(CHAIN_ID_ETHEREUM) == first


class TestTransportClassification:
    def test_reverts_and_bad_output_are_not_transport(self, sb):
        from web3.exceptions import BadFunctionCallOutput

        assert is_transport_error(ContractLogicError("execution reverted")) is False
        assert is_transport_error(BadFunctionCallOutput("0x")) is False
        assert is_transport_error(ValueError("bad registry row")) is False

    def test_http_and_socket_failures_are_transport(self, sb):
        assert is_transport_error(_http_401()) is True
        assert is_transport_error(ConnectionError("refused")) is True
        assert is_transport_error(requests.Timeout("slow")) is True


class TestKeyNeverLeaks:
    def test_redaction_strips_url_paths_and_the_key(self, sb, monkeypatch):
        monkeypatch.setattr(Config, "ALCHEMY_API_KEY", ALCHEMY_KEY)
        text = redact_rpc_error(
            f"401 for url: https://eth-mainnet.g.alchemy.com/v2/{ALCHEMY_KEY} "
            f"and https://user:pw@rpc.example/path?k=1 and bare {ALCHEMY_KEY}"
        )
        assert ALCHEMY_KEY not in text
        assert "pw@" not in text and "/path" not in text
        assert "https://eth-mainnet.g.alchemy.com/***" in text

    def test_failure_reason_and_logs_never_contain_the_key(self, sb, monkeypatch, caplog):
        monkeypatch.setattr(Config, "ALCHEMY_API_KEY", ALCHEMY_KEY)
        clients = {
            PRIMARY: _fake_client(raises=_http_401()),
            FALLBACK: _fake_client(raises=_http_401()),
        }
        with caplog.at_level(logging.DEBUG, logger="src.services.holdings.chains"):
            result, _calls = _read_with(clients, [ETH_NATIVE])

        assert result.failed_chain_ids == [CHAIN_ID_ETHEREUM]
        assert ALCHEMY_KEY not in result.failures[0].reason
        assert "401" in result.failures[0].reason
        assert ALCHEMY_KEY not in caplog.text
