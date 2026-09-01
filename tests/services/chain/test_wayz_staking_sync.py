"""Tests for src.services.chain.wayz_staking_sync (gatewayz-backend#2244)."""

from unittest.mock import MagicMock, patch

import pytest

from src.services.chain.wayz_staking_sync import sync_once


@pytest.fixture
def sb():
    return None


def _mock_client(current_block, staked_balances: dict, total_staked, staked_events):
    client = MagicMock()
    client.current_block.return_value = current_block
    client.total_staked.return_value = total_staked
    client.staked_event_addresses.return_value = staked_events
    client.staked_balance_of.side_effect = lambda addr: staked_balances[addr]
    return client


@patch("src.services.chain.wayz_staking_sync.Config")
@patch("src.services.chain.wayz_staking_sync.set_sync_cursor")
@patch("src.services.chain.wayz_staking_sync.upsert_wallet_stake")
@patch("src.services.chain.wayz_staking_sync.insert_wallet_if_missing")
@patch("src.services.chain.wayz_staking_sync.get_all_wallet_addresses")
@patch("src.services.chain.wayz_staking_sync.get_sync_cursor")
def test_first_run_scans_from_deploy_block_inclusive(
    mock_get_cursor, mock_get_all, mock_insert, mock_upsert, mock_set_cursor,
    mock_config, sb,
):
    mock_config.WAYZ_STAKING_CONTRACT_ADDRESS = "0xcontract"
    mock_config.WAYZ_STAKING_DEPLOY_BLOCK = 100
    mock_config.WAYZ_DAILY_INFERENCE_CAPACITY = 1000
    mock_get_cursor.return_value = None
    mock_get_all.return_value = []

    client = _mock_client(
        current_block=200, staked_balances={"0xabc": 500},
        total_staked=500, staked_events=["0xabc"],
    )

    result = sync_once(client)

    client.staked_event_addresses.assert_called_once_with(100, 200)
    mock_insert.assert_called_once_with("0xabc")
    mock_upsert.assert_called_once()
    upsert_args = mock_upsert.call_args[0]
    assert upsert_args[:4] == ("0xabc", 500, 1000, 200)
    assert isinstance(upsert_args[4], str) and upsert_args[4]  # ISO timestamp, non-empty
    assert result.wallets_discovered == 1
    assert result.wallets_synced == 1
    assert result.total_staked == 500
    assert result.from_block == 100
    assert result.to_block == 200
    mock_set_cursor.assert_called_once()
    cursor_args = mock_set_cursor.call_args[0]
    assert cursor_args[:2] == ("0xcontract", 200)
    assert isinstance(cursor_args[2], str) and cursor_args[2]


@patch("src.services.chain.wayz_staking_sync.Config")
@patch("src.services.chain.wayz_staking_sync.set_sync_cursor")
@patch("src.services.chain.wayz_staking_sync.upsert_wallet_stake")
@patch("src.services.chain.wayz_staking_sync.insert_wallet_if_missing")
@patch("src.services.chain.wayz_staking_sync.get_all_wallet_addresses")
@patch("src.services.chain.wayz_staking_sync.get_sync_cursor")
def test_subsequent_run_scans_from_cursor_plus_one(
    mock_get_cursor, mock_get_all, mock_insert, mock_upsert, mock_set_cursor,
    mock_config, sb,
):
    mock_config.WAYZ_STAKING_CONTRACT_ADDRESS = "0xcontract"
    mock_config.WAYZ_STAKING_DEPLOY_BLOCK = 100
    mock_config.WAYZ_DAILY_INFERENCE_CAPACITY = 1000
    mock_get_cursor.return_value = 150
    mock_get_all.return_value = ["0xabc"]

    client = _mock_client(
        current_block=200, staked_balances={"0xabc": 500},
        total_staked=500, staked_events=[],
    )

    result = sync_once(client)

    client.staked_event_addresses.assert_called_once_with(151, 200)
    mock_insert.assert_not_called()
    assert result.wallets_discovered == 0
    assert result.wallets_synced == 1
    assert result.from_block == 151  # actual scan start, not the raw cursor value


@patch("src.services.chain.wayz_staking_sync.Config")
@patch("src.services.chain.wayz_staking_sync.set_sync_cursor")
@patch("src.services.chain.wayz_staking_sync.upsert_wallet_stake")
@patch("src.services.chain.wayz_staking_sync.insert_wallet_if_missing")
@patch("src.services.chain.wayz_staking_sync.get_all_wallet_addresses")
@patch("src.services.chain.wayz_staking_sync.get_sync_cursor")
def test_zero_total_staked_gives_zero_allowance(
    mock_get_cursor, mock_get_all, mock_insert, mock_upsert, mock_set_cursor,
    mock_config, sb,
):
    mock_config.WAYZ_STAKING_CONTRACT_ADDRESS = "0xcontract"
    mock_config.WAYZ_STAKING_DEPLOY_BLOCK = 100
    mock_config.WAYZ_DAILY_INFERENCE_CAPACITY = 1000
    mock_get_cursor.return_value = 150
    mock_get_all.return_value = ["0xabc"]

    client = _mock_client(
        current_block=200, staked_balances={"0xabc": 0},
        total_staked=0, staked_events=[],
    )

    sync_once(client)

    args = mock_upsert.call_args[0]
    assert args[0] == "0xabc"
    assert args[1] == 0
    assert args[2] == 0  # daily_allowance


@patch("src.services.chain.wayz_staking_sync.Config")
@patch("src.services.chain.wayz_staking_sync.set_sync_cursor")
@patch("src.services.chain.wayz_staking_sync.upsert_wallet_stake")
@patch("src.services.chain.wayz_staking_sync.insert_wallet_if_missing")
@patch("src.services.chain.wayz_staking_sync.get_all_wallet_addresses")
@patch("src.services.chain.wayz_staking_sync.get_sync_cursor")
def test_known_wallet_is_always_resynced_even_with_no_new_events(
    mock_get_cursor, mock_get_all, mock_insert, mock_upsert, mock_set_cursor,
    mock_config, sb,
):
    """The always-re-read-every-known-wallet property is what makes this job
    double as reconciliation -- a wallet's balance is refreshed even when the
    event scan for this run found nothing."""
    mock_config.WAYZ_STAKING_CONTRACT_ADDRESS = "0xcontract"
    mock_config.WAYZ_STAKING_DEPLOY_BLOCK = 100
    mock_config.WAYZ_DAILY_INFERENCE_CAPACITY = 1000
    mock_get_cursor.return_value = 150
    mock_get_all.return_value = ["0xabc", "0xdef"]

    client = _mock_client(
        current_block=200,
        staked_balances={"0xabc": 300, "0xdef": 700},
        total_staked=1000,
        staked_events=[],
    )

    result = sync_once(client)

    assert result.wallets_synced == 2
    assert client.staked_balance_of.call_count == 2


@patch("src.services.chain.wayz_staking_sync.Config")
@patch("src.services.chain.wayz_staking_sync.set_sync_cursor")
@patch("src.services.chain.wayz_staking_sync.upsert_wallet_stake")
@patch("src.services.chain.wayz_staking_sync.insert_wallet_if_missing")
@patch("src.services.chain.wayz_staking_sync.get_all_wallet_addresses")
@patch("src.services.chain.wayz_staking_sync.get_sync_cursor")
def test_failed_discovery_insert_skips_cursor_advance(
    mock_get_cursor, mock_get_all, mock_insert, mock_upsert, mock_set_cursor,
    mock_config, sb,
):
    """A newly-discovered wallet whose insert fails must not let the cursor
    advance past the block range containing its Staked event -- otherwise
    that wallet is never rediscovered."""
    mock_config.WAYZ_STAKING_CONTRACT_ADDRESS = "0xcontract"
    mock_config.WAYZ_STAKING_DEPLOY_BLOCK = 100
    mock_config.WAYZ_DAILY_INFERENCE_CAPACITY = 1000
    mock_get_cursor.return_value = 150
    mock_get_all.return_value = []
    mock_insert.return_value = False

    client = _mock_client(
        current_block=200, staked_balances={"0xabc": 500},
        total_staked=500, staked_events=["0xabc"],
    )

    sync_once(client)

    mock_insert.assert_called_once_with("0xabc")
    mock_set_cursor.assert_not_called()


@patch("src.services.chain.wayz_staking_sync.Config")
@patch("src.services.chain.wayz_staking_sync.set_sync_cursor")
@patch("src.services.chain.wayz_staking_sync.upsert_wallet_stake")
@patch("src.services.chain.wayz_staking_sync.insert_wallet_if_missing")
@patch("src.services.chain.wayz_staking_sync.get_all_wallet_addresses")
@patch("src.services.chain.wayz_staking_sync.get_sync_cursor")
def test_one_wallet_resync_failure_does_not_abort_others(
    mock_get_cursor, mock_get_all, mock_insert, mock_upsert, mock_set_cursor,
    mock_config, sb,
):
    """A single wallet raising from staked_balance_of during resync must not
    starve the other known wallets, and since resync failures are
    self-healing next run, the cursor should still advance."""
    mock_config.WAYZ_STAKING_CONTRACT_ADDRESS = "0xcontract"
    mock_config.WAYZ_STAKING_DEPLOY_BLOCK = 100
    mock_config.WAYZ_DAILY_INFERENCE_CAPACITY = 1000
    mock_get_cursor.return_value = 150
    mock_get_all.return_value = ["0xabc", "0xdef", "0xfff"]

    client = _mock_client(
        current_block=200,
        staked_balances={"0xabc": 300, "0xdef": 700, "0xfff": 0},
        total_staked=1000,
        staked_events=[],
    )

    def _staked_balance_of(addr):
        if addr == "0xfff":
            raise RuntimeError("RPC exploded")
        return {"0xabc": 300, "0xdef": 700}[addr]

    client.staked_balance_of.side_effect = _staked_balance_of

    result = sync_once(client)

    assert result.wallets_failed == 1
    assert result.wallets_synced == 2
    upserted_addrs = {call.args[0] for call in mock_upsert.call_args_list}
    assert upserted_addrs == {"0xabc", "0xdef"}
    mock_set_cursor.assert_called_once()
