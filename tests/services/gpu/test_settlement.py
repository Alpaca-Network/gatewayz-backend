"""Tests for src.services.gpu.settlement (gatewayz-backend#2266; PR #2288
review fix round 1, I3/I4)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.chain.eth_payout_client import (
    EthUsdPrice,
    SignedTransfer,
    StalePriceError,
    TransferBroadcastError,
    TransferNotSentError,
)
from src.services.gpu.settlement import reconcile_stuck_settlements, run_settlement_once

# answer=1e12 with 0 decimals makes usd_micros_to_wei the identity
# (wei == usd_micros), so these tests can reason in one unit. Real
# conversion math is covered in tests/services/chain/test_eth_payout_client.py.
_IDENTITY_PRICE_ANSWER = 10**12


@pytest.fixture
def sb():
    return None


def _client(
    pool_balance_wei=10**30,
    transfer_result="0xtxhash",
    transfer_error=None,
    receipt=None,
    receipt_error=None,
    price=None,
    price_error=None,
    wait_receipt=None,
    confirmed_nonce=0,
):
    """A mocked EthPayoutClient. `transfer` behaves like the real one's
    contract: it calls record(signed) BEFORE 'broadcasting', and a
    record() of False raises TransferNotSentError. `transfer_error` is
    raised after recording (like a broadcast failure) unless it is a
    TransferNotSentError (raised before recording)."""
    import time

    client = MagicMock()
    if price_error is not None:
        client.trusted_eth_usd_price.side_effect = price_error
    else:
        client.trusted_eth_usd_price.return_value = price or EthUsdPrice(
            answer=_IDENTITY_PRICE_ANSWER, decimals=0, updated_at=int(time.time())
        )
    client.pool_balance_wei.return_value = pool_balance_wei

    async def _transfer(to, amount_wei, record):
        if isinstance(transfer_error, TransferNotSentError):
            raise transfer_error
        signed = SignedTransfer(
            tx_hash=transfer_result, nonce=7, gas=30_000, to=to, amount_wei=amount_wei
        )
        if not record(signed):
            raise TransferNotSentError("could not record tx hash before send")
        if transfer_error is not None:
            raise transfer_error
        return signed

    client.transfer = AsyncMock(side_effect=_transfer)
    client.wait_for_receipt.return_value = (
        wait_receipt if wait_receipt is not None else {"status": 1}
    )
    if receipt_error is not None:
        client.get_receipt.side_effect = receipt_error
    else:
        client.get_receipt.return_value = receipt
    client.confirmed_nonce.return_value = confirmed_nonce
    return client


def _provider(provider_id=1, wallet="0xwallet"):
    return {"id": provider_id, "payout_wallet_address": wallet, "status": "approved"}


def _earning(earning_id, amount_usd_micros):
    return {"id": earning_id, "amount_usd_micros": amount_usd_micros}


_PATCH_TARGETS = (
    "src.services.gpu.settlement.list_approved_providers",
    "src.services.gpu.settlement.get_pending_settlement",
    "src.services.gpu.settlement.list_accrued_earnings",
    "src.services.gpu.settlement.create_settlement",
    "src.services.gpu.settlement.mark_settlement_sent",
    "src.services.gpu.settlement.mark_settlement_failed",
    "src.services.gpu.settlement.mark_earnings_settled",
    "src.services.gpu.settlement.mark_earnings_settling",
    "src.services.gpu.settlement.mark_earnings_accrued",
    "src.services.gpu.settlement.update_settlement_amount",
    "src.services.gpu.settlement.record_settlement_tx",
)


def _patched(**overrides):
    """Context manager stack for all the gpu_payouts calls settlement.py
    makes, with sane defaults overridable per test.

    mark_earnings_settling defaults to mirroring whatever
    list_accrued_earnings would return for that provider_id -- i.e. "the
    atomic flip claimed exactly what the preview read saw, nothing raced
    it." Tests exercising the I4 race window override
    mark_earnings_settling directly to simulate a discrepancy.
    """
    from contextlib import ExitStack

    stack = ExitStack()
    mocks = {}
    for target in _PATCH_TARGETS:
        name = target.rsplit(".", 1)[-1]
        m = stack.enter_context(patch(target))
        mocks[name] = m
    mocks["get_pending_settlement"].return_value = None
    mocks["create_settlement"].return_value = {"id": 99}
    mocks["record_settlement_tx"].return_value = True
    mocks["mark_earnings_settling"].side_effect = lambda provider_id, settlement_id: mocks[
        "list_accrued_earnings"
    ](provider_id)
    for key, value in overrides.items():
        mocks[key].return_value = value
    return stack, mocks


@pytest.mark.asyncio
async def test_settlement_pays_provider_above_min_payout(sb):
    with patch("src.services.gpu.settlement.Config") as mock_config:
        mock_config.COMMUNITY_MIN_PAYOUT_USD = 10
        mock_config.COMMUNITY_MAX_PAYOUT_PER_RUN_USD = 100_000
        mock_config.COMMUNITY_SETTLEMENT_INTERVAL_HOURS = 24
        mock_config.ETH_USD_PRICE_MAX_AGE_SECONDS = 3600
        mock_config.PROVIDER_PAYOUT_GAS_RESERVE_WEI = 0

        stack, mocks = _patched(
            list_approved_providers=[_provider()],
            list_accrued_earnings=[_earning(1, 20 * 10**6)],
        )
        with stack:
            client = _client()
            result = await run_settlement_once(client)

    assert result.settlements_sent == 1
    assert result.total_sent_wei == 20 * 10**6
    client.transfer.assert_called_once()
    assert client.transfer.call_args.args[:2] == ("0xwallet", 20 * 10**6)
    mocks["mark_settlement_sent"].assert_called_once_with(99, "0xtxhash")
    mocks["mark_earnings_settled"].assert_called_once_with([1], 99)


@pytest.mark.asyncio
async def test_settlement_skips_provider_below_min_payout(sb):
    with patch("src.services.gpu.settlement.Config") as mock_config:
        mock_config.COMMUNITY_MIN_PAYOUT_USD = 10
        mock_config.COMMUNITY_MAX_PAYOUT_PER_RUN_USD = 100_000
        mock_config.COMMUNITY_SETTLEMENT_INTERVAL_HOURS = 24
        mock_config.ETH_USD_PRICE_MAX_AGE_SECONDS = 3600
        mock_config.PROVIDER_PAYOUT_GAS_RESERVE_WEI = 0

        stack, mocks = _patched(
            list_approved_providers=[_provider()],
            list_accrued_earnings=[_earning(1, 5 * 10**6)],  # below $10 min
        )
        with stack:
            client = _client()
            result = await run_settlement_once(client)

    assert result.settlements_sent == 0
    assert result.providers_skipped_below_min == 1
    client.transfer.assert_not_called()
    mocks["create_settlement"].assert_not_called()


@pytest.mark.asyncio
async def test_settlement_is_idempotent_for_a_pending_settlement(sb):
    with patch("src.services.gpu.settlement.Config") as mock_config:
        mock_config.COMMUNITY_MIN_PAYOUT_USD = 10
        mock_config.COMMUNITY_MAX_PAYOUT_PER_RUN_USD = 100_000
        mock_config.COMMUNITY_SETTLEMENT_INTERVAL_HOURS = 24
        mock_config.ETH_USD_PRICE_MAX_AGE_SECONDS = 3600
        mock_config.PROVIDER_PAYOUT_GAS_RESERVE_WEI = 0

        stack, mocks = _patched(
            list_approved_providers=[_provider()],
            get_pending_settlement={"id": 5, "status": "pending"},
            list_accrued_earnings=[_earning(1, 20 * 10**6)],
        )
        with stack:
            client = _client()
            result = await run_settlement_once(client)

    assert result.providers_skipped_pending == 1
    assert result.settlements_sent == 0
    mocks["create_settlement"].assert_not_called()
    client.transfer.assert_not_called()


@pytest.mark.asyncio
async def test_settlement_respects_per_run_cap_across_providers(sb):
    with patch("src.services.gpu.settlement.Config") as mock_config:
        mock_config.COMMUNITY_MIN_PAYOUT_USD = 10
        mock_config.COMMUNITY_MAX_PAYOUT_PER_RUN_USD = 30  # $30 cap this run
        mock_config.COMMUNITY_SETTLEMENT_INTERVAL_HOURS = 24
        mock_config.ETH_USD_PRICE_MAX_AGE_SECONDS = 3600
        mock_config.PROVIDER_PAYOUT_GAS_RESERVE_WEI = 0

        providers = [_provider(1, "0xwallet1"), _provider(2, "0xwallet2")]
        earnings_by_provider = {
            1: [_earning(1, 20 * 10**6)],
            2: [_earning(2, 20 * 10**6)],  # second provider would exceed the $30 cap
        }

        stack, mocks = _patched(list_approved_providers=providers)
        with stack:
            mocks["list_accrued_earnings"].side_effect = lambda pid: earnings_by_provider[pid]
            client = _client()
            result = await run_settlement_once(client)

    assert result.settlements_sent == 1
    assert result.providers_skipped_cap == 1
    client.transfer.assert_called_once()
    assert client.transfer.call_args.args[:2] == ("0xwallet1", 20 * 10**6)


@pytest.mark.asyncio
async def test_settlement_skips_when_pool_balance_insufficient(sb):
    with patch("src.services.gpu.settlement.Config") as mock_config:
        mock_config.COMMUNITY_MIN_PAYOUT_USD = 10
        mock_config.COMMUNITY_MAX_PAYOUT_PER_RUN_USD = 100_000
        mock_config.COMMUNITY_SETTLEMENT_INTERVAL_HOURS = 24
        mock_config.ETH_USD_PRICE_MAX_AGE_SECONDS = 3600
        mock_config.PROVIDER_PAYOUT_GAS_RESERVE_WEI = 0

        stack, mocks = _patched(
            list_approved_providers=[_provider()],
            list_accrued_earnings=[_earning(1, 20 * 10**6)],
        )
        with stack:
            client = _client(pool_balance_wei=5 * 10**6)  # less than owed
            result = await run_settlement_once(client)

    assert result.settlements_sent == 0
    assert result.providers_skipped_insufficient_pool == 1
    client.transfer.assert_not_called()
    mocks["create_settlement"].assert_not_called()


@pytest.mark.asyncio
async def test_settlement_marks_failed_and_keeps_earnings_accrued_on_transfer_error(sb):
    with patch("src.services.gpu.settlement.Config") as mock_config:
        mock_config.COMMUNITY_MIN_PAYOUT_USD = 10
        mock_config.COMMUNITY_MAX_PAYOUT_PER_RUN_USD = 100_000
        mock_config.COMMUNITY_SETTLEMENT_INTERVAL_HOURS = 24
        mock_config.ETH_USD_PRICE_MAX_AGE_SECONDS = 3600
        mock_config.PROVIDER_PAYOUT_GAS_RESERVE_WEI = 0

        stack, mocks = _patched(
            list_approved_providers=[_provider()],
            list_accrued_earnings=[_earning(1, 20 * 10**6)],
        )
        with stack:
            client = _client(transfer_error=TransferNotSentError("rpc down"))
            result = await run_settlement_once(client)

    assert result.settlements_sent == 0
    assert result.settlements_failed == 1
    mocks["mark_settlement_failed"].assert_called_once_with(99, "rpc down")
    mocks["mark_earnings_settled"].assert_not_called()
    mocks["mark_earnings_accrued"].assert_called_once_with([1], 99)


@pytest.mark.asyncio
async def test_settlement_skips_provider_with_no_payout_wallet(sb):
    with patch("src.services.gpu.settlement.Config") as mock_config:
        mock_config.COMMUNITY_MIN_PAYOUT_USD = 10
        mock_config.COMMUNITY_MAX_PAYOUT_PER_RUN_USD = 100_000
        mock_config.COMMUNITY_SETTLEMENT_INTERVAL_HOURS = 24
        mock_config.ETH_USD_PRICE_MAX_AGE_SECONDS = 3600
        mock_config.PROVIDER_PAYOUT_GAS_RESERVE_WEI = 0

        stack, mocks = _patched(
            list_approved_providers=[_provider(wallet=None)],
            list_accrued_earnings=[_earning(1, 20 * 10**6)],
        )
        with stack:
            client = _client()
            result = await run_settlement_once(client)

    assert result.settlements_sent == 0
    mocks["create_settlement"].assert_not_called()
    client.transfer.assert_not_called()


@pytest.mark.asyncio
async def test_settlement_aborts_run_when_pool_balance_lookup_fails(sb):
    with patch("src.services.gpu.settlement.Config") as mock_config:
        mock_config.COMMUNITY_MIN_PAYOUT_USD = 10
        mock_config.COMMUNITY_MAX_PAYOUT_PER_RUN_USD = 100_000
        mock_config.COMMUNITY_SETTLEMENT_INTERVAL_HOURS = 24
        mock_config.ETH_USD_PRICE_MAX_AGE_SECONDS = 3600
        mock_config.PROVIDER_PAYOUT_GAS_RESERVE_WEI = 0

        stack, mocks = _patched(list_approved_providers=[_provider()])
        with stack:
            client = MagicMock()
            client.pool_balance_wei.side_effect = RuntimeError("rpc unreachable")
            result = await run_settlement_once(client)

    assert result.providers_considered == 0
    mocks["list_accrued_earnings"].assert_not_called()


# ---------------------------------------------------------------------------
# I4: void-vs-settle race -- the atomic settling flip is authoritative,
# not the preview read
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_settlement_uses_the_atomic_flip_amount_not_the_preview(sb):
    """A concurrent spot-check void removes one of the two earnings the
    preview read saw BEFORE the atomic flip runs -- the flip must be the
    source of truth for what gets transferred and later marked settled,
    not the stale preview."""
    with patch("src.services.gpu.settlement.Config") as mock_config:
        mock_config.COMMUNITY_MIN_PAYOUT_USD = 10
        mock_config.COMMUNITY_MAX_PAYOUT_PER_RUN_USD = 100_000
        mock_config.COMMUNITY_SETTLEMENT_INTERVAL_HOURS = 24
        mock_config.ETH_USD_PRICE_MAX_AGE_SECONDS = 3600
        mock_config.PROVIDER_PAYOUT_GAS_RESERVE_WEI = 0

        stack, mocks = _patched(
            list_approved_providers=[_provider()],
            list_accrued_earnings=[_earning(1, 20 * 10**6), _earning(2, 20 * 10**6)],
        )
        with stack:
            # The atomic flip only claims earning 1 -- earning 2 was voided
            # by a concurrent spot-check failure between preview and flip.
            mocks["mark_earnings_settling"].side_effect = None
            mocks["mark_earnings_settling"].return_value = [_earning(1, 20 * 10**6)]
            client = _client()
            result = await run_settlement_once(client)

    assert result.settlements_sent == 1
    assert result.total_sent_wei == 20 * 10**6  # NOT 40 -- the flip's amount, not the preview's
    client.transfer.assert_called_once()
    assert client.transfer.call_args.args[:2] == ("0xwallet", 20 * 10**6)
    mocks["mark_earnings_settled"].assert_called_once_with([1], 99)
    mocks["update_settlement_amount"].assert_called_once_with(99, 20 * 10**6, 20 * 10**6)


@pytest.mark.asyncio
async def test_settlement_reverts_and_fails_when_flip_claims_nothing(sb):
    """The preview saw accrued earnings, but by the time of the atomic
    flip every one of them had already been voided -- must fail cleanly,
    not transfer 0 wei."""
    with patch("src.services.gpu.settlement.Config") as mock_config:
        mock_config.COMMUNITY_MIN_PAYOUT_USD = 10
        mock_config.COMMUNITY_MAX_PAYOUT_PER_RUN_USD = 100_000
        mock_config.COMMUNITY_SETTLEMENT_INTERVAL_HOURS = 24
        mock_config.ETH_USD_PRICE_MAX_AGE_SECONDS = 3600
        mock_config.PROVIDER_PAYOUT_GAS_RESERVE_WEI = 0

        stack, mocks = _patched(
            list_approved_providers=[_provider()],
            list_accrued_earnings=[_earning(1, 20 * 10**6)],
        )
        with stack:
            mocks["mark_earnings_settling"].side_effect = None
            mocks["mark_earnings_settling"].return_value = []
            client = _client()
            result = await run_settlement_once(client)

    assert result.settlements_sent == 0
    client.transfer.assert_not_called()
    mocks["mark_settlement_failed"].assert_called_once()


@pytest.mark.asyncio
async def test_settlement_reverts_when_flip_amount_falls_below_min_after_race(sb):
    """The preview cleared the minimum, but the authoritative (post-flip)
    amount doesn't -- must revert the claimed earnings back to accrued,
    not transfer below the configured minimum."""
    with patch("src.services.gpu.settlement.Config") as mock_config:
        mock_config.COMMUNITY_MIN_PAYOUT_USD = 10
        mock_config.COMMUNITY_MAX_PAYOUT_PER_RUN_USD = 100_000
        mock_config.COMMUNITY_SETTLEMENT_INTERVAL_HOURS = 24
        mock_config.ETH_USD_PRICE_MAX_AGE_SECONDS = 3600
        mock_config.PROVIDER_PAYOUT_GAS_RESERVE_WEI = 0

        stack, mocks = _patched(
            list_approved_providers=[_provider()],
            list_accrued_earnings=[_earning(1, 20 * 10**6), _earning(2, 20 * 10**6)],
        )
        with stack:
            # Only a tiny sliver survived the race -- below the $10 minimum.
            mocks["mark_earnings_settling"].side_effect = None
            mocks["mark_earnings_settling"].return_value = [_earning(1, 1 * 10**6)]
            client = _client()
            result = await run_settlement_once(client)

    assert result.settlements_sent == 0
    assert result.providers_skipped_below_min == 1
    client.transfer.assert_not_called()
    mocks["mark_earnings_accrued"].assert_called_once_with([1], 99)
    mocks["mark_settlement_failed"].assert_called_once()


# ---------------------------------------------------------------------------
# I3: stuck-pending settlement reconciliation
# ---------------------------------------------------------------------------


_RECONCILE_TARGETS = (
    "src.services.gpu.settlement.list_pending_settlements",
    "src.services.gpu.settlement.list_settling_earnings_for_settlement",
    "src.services.gpu.settlement.mark_settlement_sent",
    "src.services.gpu.settlement.mark_settlement_failed",
    "src.services.gpu.settlement.mark_earnings_settled",
    "src.services.gpu.settlement.mark_earnings_accrued",
)


def _patched_reconcile(**overrides):
    from contextlib import ExitStack

    stack = ExitStack()
    mocks = {}
    for target in _RECONCILE_TARGETS:
        name = target.rsplit(".", 1)[-1]
        mocks[name] = stack.enter_context(patch(target))
    mocks["list_settling_earnings_for_settlement"].return_value = [_earning(1, 20 * 10**6)]
    for key, value in overrides.items():
        mocks[key].return_value = value
    return stack, mocks


@pytest.mark.asyncio
async def test_reconcile_confirms_a_stuck_settlement_with_a_successful_receipt(sb):
    stuck = {"id": 5, "tx_hash": "0xabc", "provider_id": 1}
    stack, mocks = _patched_reconcile(list_pending_settlements=[stuck])
    with stack:
        client = _client(receipt={"status": 1, "transactionHash": "0xabc"})
        result = await reconcile_stuck_settlements(client)

    assert result.settlements_confirmed_sent == 1
    assert result.settlements_marked_failed == 0
    mocks["mark_settlement_sent"].assert_called_once_with(5, "0xabc")
    mocks["mark_earnings_settled"].assert_called_once_with([1], 5)
    mocks["mark_earnings_accrued"].assert_not_called()


@pytest.mark.asyncio
async def test_reconcile_fails_and_reverts_when_receipt_shows_a_revert(sb):
    stuck = {"id": 5, "tx_hash": "0xabc", "provider_id": 1}
    stack, mocks = _patched_reconcile(list_pending_settlements=[stuck])
    with stack:
        client = _client(receipt={"status": 0, "transactionHash": "0xabc"})
        result = await reconcile_stuck_settlements(client)

    assert result.settlements_marked_failed == 1
    mocks["mark_settlement_failed"].assert_called_once()
    mocks["mark_earnings_accrued"].assert_called_once_with([1], 5)
    mocks["mark_settlement_sent"].assert_not_called()


@pytest.mark.asyncio
async def test_reconcile_fails_and_reverts_when_no_tx_hash_was_ever_recorded(sb):
    stuck = {"id": 6, "tx_hash": None, "provider_id": 1, "created_at": "2020-01-01T00:00:00+00:00"}
    stack, mocks = _patched_reconcile(list_pending_settlements=[stuck])
    with stack:
        client = _client()
        result = await reconcile_stuck_settlements(client)

    assert result.settlements_marked_failed == 1
    mocks["mark_earnings_accrued"].assert_called_once_with([1], 6)
    client.get_receipt.assert_not_called()


@pytest.mark.asyncio
async def test_reconcile_leaves_pending_when_no_receipt_and_nonce_not_consumed(sb):
    """PR #2364 review: a recorded hash with no receipt whose nonce is NOT
    yet consumed could still land -- it must stay pending, never reverted
    (reverting here is exactly the double-pay the review flagged)."""
    stuck = {
        "id": 7,
        "tx_hash": "0xdeadbeef",
        "tx_nonce": 12,
        "provider_id": 1,
        "created_at": "2020-01-01T00:00:00+00:00",
    }
    stack, mocks = _patched_reconcile(list_pending_settlements=[stuck])
    with stack:
        client = _client(receipt=None, confirmed_nonce=12)  # nonce 12 not mined yet
        result = await reconcile_stuck_settlements(client)

    assert result.settlements_left_pending == 1
    assert result.settlements_marked_failed == 0
    mocks["mark_settlement_failed"].assert_not_called()
    mocks["mark_earnings_accrued"].assert_not_called()
    mocks["mark_earnings_settled"].assert_not_called()


@pytest.mark.asyncio
async def test_reconcile_fails_and_reverts_when_nonce_was_consumed_by_another_tx(sb):
    """No receipt for our hash, but the pool's mined nonce has moved past
    it -- another tx took that nonce, so ours can never land. Safe to
    revert (after one receipt re-check)."""
    stuck = {"id": 8, "tx_hash": "0xdeadbeef", "tx_nonce": 12, "provider_id": 1}
    stack, mocks = _patched_reconcile(list_pending_settlements=[stuck])
    with stack:
        client = _client(receipt=None, confirmed_nonce=13)
        result = await reconcile_stuck_settlements(client)

    assert result.settlements_marked_failed == 1
    assert client.get_receipt.call_count == 2  # re-checked before reverting
    mocks["mark_earnings_accrued"].assert_called_once_with([1], 8)
    mocks["mark_settlement_sent"].assert_not_called()


@pytest.mark.asyncio
async def test_reconcile_confirms_via_receipt_on_the_recheck_after_nonce_moved(sb):
    stuck = {"id": 9, "tx_hash": "0xabc", "tx_nonce": 12, "provider_id": 1}
    stack, mocks = _patched_reconcile(list_pending_settlements=[stuck])
    with stack:
        client = _client(confirmed_nonce=13)
        client.get_receipt.side_effect = [None, {"status": 1}]
        result = await reconcile_stuck_settlements(client)

    assert result.settlements_confirmed_sent == 1
    mocks["mark_earnings_settled"].assert_called_once_with([1], 9)
    mocks["mark_earnings_accrued"].assert_not_called()


@pytest.mark.asyncio
async def test_reconcile_skips_a_young_pending_row_without_a_tx_hash(sb):
    """A run may still be between create_settlement and recording the
    hash -- a fresh no-hash row is left alone."""
    from datetime import UTC, datetime

    fresh = {
        "id": 10,
        "tx_hash": None,
        "provider_id": 1,
        "created_at": datetime.now(UTC).isoformat(),
    }
    stack, mocks = _patched_reconcile(list_pending_settlements=[fresh])
    with stack:
        client = _client()
        result = await reconcile_stuck_settlements(client)

    assert result.settlements_checked == 0
    mocks["mark_settlement_failed"].assert_not_called()
    mocks["mark_earnings_accrued"].assert_not_called()


@pytest.mark.asyncio
async def test_reconcile_resolves_a_young_row_with_a_tx_hash_by_receipt(sb):
    """Rows WITH a hash are resolved by receipt at any age (no 2h wait)."""
    from datetime import UTC, datetime

    fresh = {
        "id": 11,
        "tx_hash": "0xabc",
        "provider_id": 1,
        "created_at": datetime.now(UTC).isoformat(),
    }
    stack, mocks = _patched_reconcile(list_pending_settlements=[fresh])
    with stack:
        client = _client(receipt={"status": 1})
        result = await reconcile_stuck_settlements(client)

    assert result.settlements_confirmed_sent == 1
    mocks["mark_settlement_sent"].assert_called_once_with(11, "0xabc")


@pytest.mark.asyncio
async def test_reconcile_leaves_pending_when_receipt_lookup_errors(sb):
    stuck = {"id": 12, "tx_hash": "0xabc", "tx_nonce": 3, "provider_id": 1}
    stack, mocks = _patched_reconcile(list_pending_settlements=[stuck])
    with stack:
        client = _client(receipt_error=RuntimeError("rpc 502"))
        result = await reconcile_stuck_settlements(client)

    assert result.settlements_left_pending == 1
    mocks["mark_settlement_failed"].assert_not_called()
    mocks["mark_earnings_accrued"].assert_not_called()


@pytest.mark.asyncio
async def test_reconcile_is_a_noop_when_nothing_is_stuck(sb):
    stack, mocks = _patched_reconcile(list_pending_settlements=[])
    with stack:
        client = _client()
        result = await reconcile_stuck_settlements(client)

    assert result.settlements_checked == 0
    mocks["mark_settlement_sent"].assert_not_called()
    mocks["mark_settlement_failed"].assert_not_called()


@pytest.mark.asyncio
async def test_settlement_pays_an_emission_mode_earning_with_null_work_id(sb):
    """Chutes-style WAYZ emission rewards (gatewayz-backend tokenomics):
    an emission allocation (source='emission', work_id=NULL, epoch_date
    set) is summed, claimed, and transferred exactly like a per_unit
    earning -- settlement.py never selects or filters on work_id, so a
    null value here needs no special-casing. See
    docs/gpu/VERIFICATION_AND_PAYOUTS.md's "Settlement" section."""
    emission_earning = {
        "id": 1,
        "amount_usd_micros": 20 * 10**6,
        "work_id": None,
        "source": "emission",
        "epoch_date": "2026-09-12",
    }
    with patch("src.services.gpu.settlement.Config") as mock_config:
        mock_config.COMMUNITY_MIN_PAYOUT_USD = 10
        mock_config.COMMUNITY_MAX_PAYOUT_PER_RUN_USD = 100_000
        mock_config.COMMUNITY_SETTLEMENT_INTERVAL_HOURS = 24
        mock_config.ETH_USD_PRICE_MAX_AGE_SECONDS = 3600
        mock_config.PROVIDER_PAYOUT_GAS_RESERVE_WEI = 0

        stack, mocks = _patched(
            list_approved_providers=[_provider()],
            list_accrued_earnings=[emission_earning],
        )
        with stack:
            client = _client()
            result = await run_settlement_once(client)

    assert result.settlements_sent == 1
    assert result.total_sent_wei == 20 * 10**6
    client.transfer.assert_called_once()
    assert client.transfer.call_args.args[:2] == ("0xwallet", 20 * 10**6)
    mocks["mark_earnings_settled"].assert_called_once_with([1], 99)


# ---------------------------------------------------------------------------
# ETH-on-Base specifics (2026-09-22): price gate, real conversion, gas reserve
# ---------------------------------------------------------------------------


def _eth_config(mock_config):
    mock_config.COMMUNITY_MIN_PAYOUT_USD = "10"
    mock_config.COMMUNITY_MAX_PAYOUT_PER_RUN_USD = "5000"
    mock_config.COMMUNITY_SETTLEMENT_INTERVAL_HOURS = 24
    mock_config.ETH_USD_PRICE_MAX_AGE_SECONDS = 3600
    mock_config.PROVIDER_PAYOUT_GAS_RESERVE_WEI = 0


@pytest.mark.asyncio
async def test_settlement_aborts_without_paying_when_price_is_stale(sb):
    with patch("src.services.gpu.settlement.Config") as mock_config:
        _eth_config(mock_config)
        stack, mocks = _patched(
            list_approved_providers=[_provider()],
            list_accrued_earnings=[_earning(1, 20 * 10**6)],
        )
        with stack:
            client = _client(price_error=StalePriceError("ETH/USD feed answer is 7200s old"))
            result = await run_settlement_once(client)

    assert result.aborted_reason is not None
    assert result.aborted_reason.startswith("stale_price")
    client.transfer.assert_not_called()
    mocks["create_settlement"].assert_not_called()
    mocks["mark_earnings_settling"].assert_not_called()


@pytest.mark.asyncio
async def test_settlement_aborts_without_paying_when_price_read_fails(sb):
    with patch("src.services.gpu.settlement.Config") as mock_config:
        _eth_config(mock_config)
        stack, mocks = _patched(
            list_approved_providers=[_provider()],
            list_accrued_earnings=[_earning(1, 20 * 10**6)],
        )
        with stack:
            client = _client(price_error=RuntimeError("rpc down"))
            result = await run_settlement_once(client)

    assert result.aborted_reason.startswith("price_unavailable")
    client.transfer.assert_not_called()
    mocks["create_settlement"].assert_not_called()


@pytest.mark.asyncio
async def test_settlement_converts_usd_to_eth_wei_at_the_feed_price(sb):
    """$30 at $3000/ETH (Chainlink 8-decimals answer) == 0.01 ETH."""
    import time

    with patch("src.services.gpu.settlement.Config") as mock_config:
        _eth_config(mock_config)
        stack, mocks = _patched(
            list_approved_providers=[_provider()],
            list_accrued_earnings=[_earning(1, 30 * 10**6)],
        )
        with stack:
            price = EthUsdPrice(answer=3000 * 10**8, decimals=8, updated_at=int(time.time()))
            client = _client(pool_balance_wei=10**18, price=price)
            result = await run_settlement_once(client)

    assert result.settlements_sent == 1
    assert result.total_sent_usd_micros == 30 * 10**6
    assert result.total_sent_wei == 10**16
    assert result.eth_usd_price == "3000"
    client.transfer.assert_called_once()
    assert client.transfer.call_args.args[:2] == ("0xwallet", 10**16)
    args = mocks["create_settlement"].call_args.args
    # (provider_id, period_start, period_end, usd_micros, wei, price, price_updated_at)
    assert args[0] == 1
    assert args[3] == 30 * 10**6
    assert args[4] == 10**16
    assert args[5] == "3000"


@pytest.mark.asyncio
async def test_settlement_keeps_a_gas_reserve_in_the_pool(sb):
    """Pool holds exactly the payout but the gas reserve must stay behind --
    the provider is deferred (insufficient pool), not paid."""
    import time

    with patch("src.services.gpu.settlement.Config") as mock_config:
        _eth_config(mock_config)
        mock_config.PROVIDER_PAYOUT_GAS_RESERVE_WEI = 10**15
        stack, mocks = _patched(
            list_approved_providers=[_provider()],
            list_accrued_earnings=[_earning(1, 30 * 10**6)],
        )
        with stack:
            price = EthUsdPrice(answer=3000 * 10**8, decimals=8, updated_at=int(time.time()))
            client = _client(pool_balance_wei=10**16, price=price)
            result = await run_settlement_once(client)

    assert result.providers_skipped_insufficient_pool == 1
    client.transfer.assert_not_called()
    mocks["create_settlement"].assert_not_called()


# ---------------------------------------------------------------------------
# PR #2364 review: confirm on receipt, never revert a possibly-landed tx
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_settlement_records_the_tx_hash_before_broadcast(sb):
    with patch("src.services.gpu.settlement.Config") as mock_config:
        _eth_config(mock_config)
        mock_config.PROVIDER_PAYOUT_RECEIPT_TIMEOUT_SECONDS = 120
        stack, mocks = _patched(
            list_approved_providers=[_provider()],
            list_accrued_earnings=[_earning(1, 20 * 10**6)],
        )
        with stack:
            client = _client(transfer_result="0xsignedhash")
            await run_settlement_once(client)

    mocks["record_settlement_tx"].assert_called_once_with(99, "0xsignedhash", 7, 30_000)
    mocks["mark_settlement_sent"].assert_called_once_with(99, "0xsignedhash")


@pytest.mark.asyncio
async def test_settlement_does_not_send_when_the_hash_cannot_be_recorded(sb):
    with patch("src.services.gpu.settlement.Config") as mock_config:
        _eth_config(mock_config)
        stack, mocks = _patched(
            list_approved_providers=[_provider()],
            list_accrued_earnings=[_earning(1, 20 * 10**6)],
        )
        mocks["record_settlement_tx"].return_value = False
        with stack:
            client = _client()
            result = await run_settlement_once(client)

    assert result.settlements_failed == 1
    client.wait_for_receipt.assert_not_called()
    mocks["mark_earnings_accrued"].assert_called_once_with([1], 99)
    mocks["mark_earnings_settled"].assert_not_called()


@pytest.mark.asyncio
async def test_settlement_leaves_pending_when_broadcast_outcome_is_unknown(sb):
    """PR #2364 review #2: send_raw_transaction raised AFTER the hash was
    recorded -- the node may have accepted it. Earnings must stay
    'settling' and the settlement 'pending' (no revert, no fail)."""
    with patch("src.services.gpu.settlement.Config") as mock_config:
        _eth_config(mock_config)
        stack, mocks = _patched(
            list_approved_providers=[_provider()],
            list_accrued_earnings=[_earning(1, 20 * 10**6)],
        )
        with stack:
            client = _client(
                transfer_result="0xmaybe",
                transfer_error=TransferBroadcastError("0xmaybe", 7, TimeoutError("read timeout")),
            )
            result = await run_settlement_once(client)

    assert result.settlements_unconfirmed == 1
    assert result.settlements_failed == 0
    mocks["record_settlement_tx"].assert_called_once_with(99, "0xmaybe", 7, 30_000)
    mocks["mark_settlement_failed"].assert_not_called()
    mocks["mark_earnings_accrued"].assert_not_called()
    mocks["mark_earnings_settled"].assert_not_called()


@pytest.mark.asyncio
async def test_settlement_does_not_settle_until_a_receipt_confirms(sb):
    """PR #2364 review #1: broadcast alone is not payment. No receipt
    within the timeout -> left pending for reconcile, never 'settled'."""
    with patch("src.services.gpu.settlement.Config") as mock_config:
        _eth_config(mock_config)
        mock_config.PROVIDER_PAYOUT_RECEIPT_TIMEOUT_SECONDS = 120
        stack, mocks = _patched(
            list_approved_providers=[_provider()],
            list_accrued_earnings=[_earning(1, 20 * 10**6)],
        )
        with stack:
            client = _client()
            client.wait_for_receipt.return_value = None
            result = await run_settlement_once(client)

    assert result.settlements_sent == 0
    assert result.settlements_unconfirmed == 1
    mocks["mark_settlement_sent"].assert_not_called()
    mocks["mark_earnings_settled"].assert_not_called()
    mocks["mark_earnings_accrued"].assert_not_called()


@pytest.mark.asyncio
async def test_settlement_reverts_earnings_when_the_tx_reverts_on_chain(sb):
    """PR #2364 review #1: e.g. an out-of-gas transfer to a smart-contract
    wallet mines with status 0 -- no ETH moved, so the earnings go back to
    'accrued' and the settlement is failed, not 'sent'."""
    with patch("src.services.gpu.settlement.Config") as mock_config:
        _eth_config(mock_config)
        stack, mocks = _patched(
            list_approved_providers=[_provider()],
            list_accrued_earnings=[_earning(1, 20 * 10**6)],
        )
        with stack:
            client = _client(wait_receipt={"status": 0})
            result = await run_settlement_once(client)

    assert result.settlements_sent == 0
    assert result.settlements_failed == 1
    assert result.total_sent_wei == 0
    mocks["mark_settlement_sent"].assert_not_called()
    mocks["mark_earnings_settled"].assert_not_called()
    mocks["mark_earnings_accrued"].assert_called_once_with([1], 99)
    assert "reverted" in mocks["mark_settlement_failed"].call_args.args[1]
