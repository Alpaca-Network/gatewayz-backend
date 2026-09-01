"""Tests for src.services.chain.wayz_staking_client (gatewayz-backend#2244)."""

from unittest.mock import MagicMock, patch

import pytest

from src.services.chain.wayz_staking_client import WayzStakingClient, WayzStakingClientError


@pytest.fixture
def sb():
    """No-op fixture whose mere presence bypasses the autouse DB-skip in
    tests/conftest.py -- this is a pure unit test with everything mocked."""
    return None


def _make_client_with_mocked_web3():
    with patch("src.services.chain.wayz_staking_client.Web3") as mock_web3_cls:
        mock_w3 = MagicMock()
        mock_web3_cls.return_value = mock_w3
        mock_web3_cls.to_checksum_address.side_effect = lambda a: a
        mock_contract = MagicMock()
        mock_w3.eth.contract.return_value = mock_contract
        client = WayzStakingClient("http://fake-rpc", "0xcontract")
        return client, mock_w3, mock_contract


def test_current_block_reads_eth_block_number(sb):
    client, mock_w3, _ = _make_client_with_mocked_web3()
    mock_w3.eth.block_number = 12345
    assert client.current_block() == 12345


def test_staked_balance_of_calls_contract_function(sb):
    client, _, mock_contract = _make_client_with_mocked_web3()
    mock_contract.functions.stakedBalanceOf.return_value.call.return_value = 500
    assert client.staked_balance_of("0xabc") == 500
    mock_contract.functions.stakedBalanceOf.assert_called_once_with("0xabc")


def test_total_staked_calls_contract_function(sb):
    client, _, mock_contract = _make_client_with_mocked_web3()
    mock_contract.functions.totalStaked.return_value.call.return_value = 1000
    assert client.total_staked() == 1000


def test_staked_event_addresses_deduplicates_lowercases_and_sorts(sb):
    client, _, mock_contract = _make_client_with_mocked_web3()
    mock_contract.events.Staked.return_value.get_logs.return_value = [
        {"args": {"staker": "0xABC"}},
        {"args": {"staker": "0xabc"}},
        {"args": {"staker": "0xDEF"}},
    ]
    result = client.staked_event_addresses(1, 100)
    assert result == ["0xabc", "0xdef"]
    mock_contract.events.Staked.return_value.get_logs.assert_called_once_with(
        from_block=1, to_block=100
    )


def test_staked_event_addresses_chunks_large_ranges(sb):
    client, _, mock_contract = _make_client_with_mocked_web3()
    start = 1000
    end = start + 5000 - 1  # 5000-block range, 2000-block chunks -> 3 calls
    mock_contract.events.Staked.return_value.get_logs.side_effect = [
        [{"args": {"staker": "0xAAA"}}],
        [{"args": {"staker": "0xbbb"}}],
        [{"args": {"staker": "0xAAA"}}],  # dup across chunks
    ]

    result = client.staked_event_addresses(start, end)

    assert result == ["0xaaa", "0xbbb"]
    get_logs = mock_contract.events.Staked.return_value.get_logs
    assert get_logs.call_count == 3
    get_logs.assert_any_call(from_block=start, to_block=start + 1999)
    get_logs.assert_any_call(from_block=start + 2000, to_block=start + 3999)
    get_logs.assert_any_call(from_block=start + 4000, to_block=end)


def test_staked_event_addresses_returns_empty_for_invalid_range(sb):
    client, _, mock_contract = _make_client_with_mocked_web3()
    result = client.staked_event_addresses(100, 1)
    assert result == []
    mock_contract.events.Staked.return_value.get_logs.assert_not_called()


def test_get_logs_call_uses_real_web3_signature(sb):
    """Regression guard for a real bug: staked_event_addresses() previously
    called get_logs(fromBlock=..., toBlock=...), but web3.py 7.x's
    ContractEvent.get_logs() only accepts from_block/to_block (snake_case).
    Every mocked test above passed anyway because MagicMock silently accepts
    any keyword name.

    This test goes through the REAL, PRODUCTION call site --
    WayzStakingClient.staked_event_addresses() itself, unmocked, against an
    unreachable host -- rather than hand-rolling a separate get_logs() call
    (an earlier version of this test did that, which decoupled it from
    whatever kwargs the real code actually uses -- it would have kept
    passing even if fromBlock/toBlock were reintroduced into
    wayz_staking_client.py). A wrong keyword name fails fast with TypeError
    (argument binding, no network attempted); the correct keywords fail with
    a connection error instead, proving get_logs actually accepted the call.
    """
    client = WayzStakingClient("http://127.0.0.1:1", "0x0000000000000000000000000000000000000001")

    with pytest.raises(Exception) as exc_info:
        client.staked_event_addresses(1, 100)

    assert not isinstance(
        exc_info.value, TypeError
    ), f"staked_event_addresses's get_logs call rejected its keyword arguments: {exc_info.value}"


def test_from_config_raises_when_contract_address_unset(sb):
    with patch("src.services.chain.wayz_staking_client.Config") as mock_config:
        mock_config.WAYZ_STAKING_CONTRACT_ADDRESS = None
        with pytest.raises(WayzStakingClientError):
            WayzStakingClient.from_config()


def test_from_config_builds_client_when_contract_address_set(sb):
    with patch("src.services.chain.wayz_staking_client.Config") as mock_config:
        mock_config.WAYZ_STAKING_CONTRACT_ADDRESS = "0xcontract"
        mock_config.AVALANCHE_FUJI_RPC_URL = "http://fake-rpc"
        with patch("src.services.chain.wayz_staking_client.Web3") as mock_web3_cls:
            mock_web3_cls.return_value = MagicMock()
            mock_web3_cls.to_checksum_address.side_effect = lambda a: a
            client = WayzStakingClient.from_config()
            assert isinstance(client, WayzStakingClient)
