"""Tests for src.services.gpu.settlement (gatewayz-backend#2266; PR #2288
review fix round 1, I3/I4)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.gpu.settlement import reconcile_stuck_settlements, run_settlement_once


@pytest.fixture
def sb():
    return None


def _client(
    pool_balance_wei=10**30,
    transfer_result="0xtxhash",
    transfer_error=None,
    receipt=None,
    receipt_error=None,
):
    client = MagicMock()
    client.pool_balance_wei.return_value = pool_balance_wei
    if transfer_error is not None:
        client.transfer = AsyncMock(side_effect=transfer_error)
    else:
        client.transfer = AsyncMock(return_value=transfer_result)
    if receipt_error is not None:
        client.get_receipt.side_effect = receipt_error
    else:
        client.get_receipt.return_value = receipt
    return client


def _provider(provider_id=1, wallet="0xwallet"):
    return {"id": provider_id, "payout_wallet_address": wallet, "status": "approved"}


def _earning(earning_id, amount_wei):
    return {"id": earning_id, "amount_wei": str(amount_wei)}


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
    mocks["mark_earnings_settling"].side_effect = lambda provider_id, settlement_id: mocks[
        "list_accrued_earnings"
    ](provider_id)
    for key, value in overrides.items():
        mocks[key].return_value = value
    return stack, mocks


@pytest.mark.asyncio
async def test_settlement_pays_provider_above_min_payout(sb):
    with patch("src.services.gpu.settlement.Config") as mock_config:
        mock_config.COMMUNITY_MIN_PAYOUT_WAYZ = 10
        mock_config.COMMUNITY_MAX_PAYOUT_PER_RUN_WAYZ = 100_000
        mock_config.COMMUNITY_SETTLEMENT_INTERVAL_HOURS = 24

        stack, mocks = _patched(
            list_approved_providers=[_provider()],
            list_accrued_earnings=[_earning(1, 20 * 10**18)],
        )
        with stack:
            client = _client()
            result = await run_settlement_once(client)

    assert result.settlements_sent == 1
    assert result.total_sent_wei == 20 * 10**18
    client.transfer.assert_called_once_with("0xwallet", 20 * 10**18)
    mocks["mark_settlement_sent"].assert_called_once_with(99, "0xtxhash")
    mocks["mark_earnings_settled"].assert_called_once_with([1], 99)


@pytest.mark.asyncio
async def test_settlement_skips_provider_below_min_payout(sb):
    with patch("src.services.gpu.settlement.Config") as mock_config:
        mock_config.COMMUNITY_MIN_PAYOUT_WAYZ = 10
        mock_config.COMMUNITY_MAX_PAYOUT_PER_RUN_WAYZ = 100_000
        mock_config.COMMUNITY_SETTLEMENT_INTERVAL_HOURS = 24

        stack, mocks = _patched(
            list_approved_providers=[_provider()],
            list_accrued_earnings=[_earning(1, 5 * 10**18)],  # below 10 WAYZ min
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
        mock_config.COMMUNITY_MIN_PAYOUT_WAYZ = 10
        mock_config.COMMUNITY_MAX_PAYOUT_PER_RUN_WAYZ = 100_000
        mock_config.COMMUNITY_SETTLEMENT_INTERVAL_HOURS = 24

        stack, mocks = _patched(
            list_approved_providers=[_provider()],
            get_pending_settlement={"id": 5, "status": "pending"},
            list_accrued_earnings=[_earning(1, 20 * 10**18)],
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
        mock_config.COMMUNITY_MIN_PAYOUT_WAYZ = 10
        mock_config.COMMUNITY_MAX_PAYOUT_PER_RUN_WAYZ = 30  # 30 WAYZ cap this run
        mock_config.COMMUNITY_SETTLEMENT_INTERVAL_HOURS = 24

        providers = [_provider(1, "0xwallet1"), _provider(2, "0xwallet2")]
        earnings_by_provider = {
            1: [_earning(1, 20 * 10**18)],
            2: [_earning(2, 20 * 10**18)],  # second provider would exceed the 30 WAYZ cap
        }

        stack, mocks = _patched(list_approved_providers=providers)
        with stack:
            mocks["list_accrued_earnings"].side_effect = lambda pid: earnings_by_provider[pid]
            client = _client()
            result = await run_settlement_once(client)

    assert result.settlements_sent == 1
    assert result.providers_skipped_cap == 1
    client.transfer.assert_called_once_with("0xwallet1", 20 * 10**18)


@pytest.mark.asyncio
async def test_settlement_skips_when_pool_balance_insufficient(sb):
    with patch("src.services.gpu.settlement.Config") as mock_config:
        mock_config.COMMUNITY_MIN_PAYOUT_WAYZ = 10
        mock_config.COMMUNITY_MAX_PAYOUT_PER_RUN_WAYZ = 100_000
        mock_config.COMMUNITY_SETTLEMENT_INTERVAL_HOURS = 24

        stack, mocks = _patched(
            list_approved_providers=[_provider()],
            list_accrued_earnings=[_earning(1, 20 * 10**18)],
        )
        with stack:
            client = _client(pool_balance_wei=5 * 10**18)  # less than owed
            result = await run_settlement_once(client)

    assert result.settlements_sent == 0
    assert result.providers_skipped_insufficient_pool == 1
    client.transfer.assert_not_called()
    mocks["create_settlement"].assert_not_called()


@pytest.mark.asyncio
async def test_settlement_marks_failed_and_keeps_earnings_accrued_on_transfer_error(sb):
    with patch("src.services.gpu.settlement.Config") as mock_config:
        mock_config.COMMUNITY_MIN_PAYOUT_WAYZ = 10
        mock_config.COMMUNITY_MAX_PAYOUT_PER_RUN_WAYZ = 100_000
        mock_config.COMMUNITY_SETTLEMENT_INTERVAL_HOURS = 24

        stack, mocks = _patched(
            list_approved_providers=[_provider()],
            list_accrued_earnings=[_earning(1, 20 * 10**18)],
        )
        with stack:
            client = _client(transfer_error=RuntimeError("rpc down"))
            result = await run_settlement_once(client)

    assert result.settlements_sent == 0
    assert result.settlements_failed == 1
    mocks["mark_settlement_failed"].assert_called_once_with(99, "rpc down")
    mocks["mark_earnings_settled"].assert_not_called()
    mocks["mark_earnings_accrued"].assert_called_once_with([1], 99)


@pytest.mark.asyncio
async def test_settlement_skips_provider_with_no_payout_wallet(sb):
    with patch("src.services.gpu.settlement.Config") as mock_config:
        mock_config.COMMUNITY_MIN_PAYOUT_WAYZ = 10
        mock_config.COMMUNITY_MAX_PAYOUT_PER_RUN_WAYZ = 100_000
        mock_config.COMMUNITY_SETTLEMENT_INTERVAL_HOURS = 24

        stack, mocks = _patched(
            list_approved_providers=[_provider(wallet=None)],
            list_accrued_earnings=[_earning(1, 20 * 10**18)],
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
        mock_config.COMMUNITY_MIN_PAYOUT_WAYZ = 10
        mock_config.COMMUNITY_MAX_PAYOUT_PER_RUN_WAYZ = 100_000
        mock_config.COMMUNITY_SETTLEMENT_INTERVAL_HOURS = 24

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
        mock_config.COMMUNITY_MIN_PAYOUT_WAYZ = 10
        mock_config.COMMUNITY_MAX_PAYOUT_PER_RUN_WAYZ = 100_000
        mock_config.COMMUNITY_SETTLEMENT_INTERVAL_HOURS = 24

        stack, mocks = _patched(
            list_approved_providers=[_provider()],
            list_accrued_earnings=[_earning(1, 20 * 10**18), _earning(2, 20 * 10**18)],
        )
        with stack:
            # The atomic flip only claims earning 1 -- earning 2 was voided
            # by a concurrent spot-check failure between preview and flip.
            mocks["mark_earnings_settling"].side_effect = None
            mocks["mark_earnings_settling"].return_value = [_earning(1, 20 * 10**18)]
            client = _client()
            result = await run_settlement_once(client)

    assert result.settlements_sent == 1
    assert result.total_sent_wei == 20 * 10**18  # NOT 40 -- the flip's amount, not the preview's
    client.transfer.assert_called_once_with("0xwallet", 20 * 10**18)
    mocks["mark_earnings_settled"].assert_called_once_with([1], 99)
    mocks["update_settlement_amount"].assert_called_once_with(99, 20 * 10**18)


@pytest.mark.asyncio
async def test_settlement_reverts_and_fails_when_flip_claims_nothing(sb):
    """The preview saw accrued earnings, but by the time of the atomic
    flip every one of them had already been voided -- must fail cleanly,
    not transfer 0 wei."""
    with patch("src.services.gpu.settlement.Config") as mock_config:
        mock_config.COMMUNITY_MIN_PAYOUT_WAYZ = 10
        mock_config.COMMUNITY_MAX_PAYOUT_PER_RUN_WAYZ = 100_000
        mock_config.COMMUNITY_SETTLEMENT_INTERVAL_HOURS = 24

        stack, mocks = _patched(
            list_approved_providers=[_provider()],
            list_accrued_earnings=[_earning(1, 20 * 10**18)],
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
        mock_config.COMMUNITY_MIN_PAYOUT_WAYZ = 10
        mock_config.COMMUNITY_MAX_PAYOUT_PER_RUN_WAYZ = 100_000
        mock_config.COMMUNITY_SETTLEMENT_INTERVAL_HOURS = 24

        stack, mocks = _patched(
            list_approved_providers=[_provider()],
            list_accrued_earnings=[_earning(1, 20 * 10**18), _earning(2, 20 * 10**18)],
        )
        with stack:
            # Only a tiny sliver survived the race -- below the 10 WAYZ minimum.
            mocks["mark_earnings_settling"].side_effect = None
            mocks["mark_earnings_settling"].return_value = [_earning(1, 1 * 10**18)]
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
    "src.services.gpu.settlement.list_stuck_pending_settlements",
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
    mocks["list_settling_earnings_for_settlement"].return_value = [_earning(1, 20 * 10**18)]
    for key, value in overrides.items():
        mocks[key].return_value = value
    return stack, mocks


@pytest.mark.asyncio
async def test_reconcile_confirms_a_stuck_settlement_with_a_successful_receipt(sb):
    stuck = {"id": 5, "tx_hash": "0xabc", "provider_id": 1}
    stack, mocks = _patched_reconcile(list_stuck_pending_settlements=[stuck])
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
    stack, mocks = _patched_reconcile(list_stuck_pending_settlements=[stuck])
    with stack:
        client = _client(receipt={"status": 0, "transactionHash": "0xabc"})
        result = await reconcile_stuck_settlements(client)

    assert result.settlements_marked_failed == 1
    mocks["mark_settlement_failed"].assert_called_once()
    mocks["mark_earnings_accrued"].assert_called_once_with([1], 5)
    mocks["mark_settlement_sent"].assert_not_called()


@pytest.mark.asyncio
async def test_reconcile_fails_and_reverts_when_no_tx_hash_was_ever_recorded(sb):
    stuck = {"id": 6, "tx_hash": None, "provider_id": 1}
    stack, mocks = _patched_reconcile(list_stuck_pending_settlements=[stuck])
    with stack:
        client = _client()
        result = await reconcile_stuck_settlements(client)

    assert result.settlements_marked_failed == 1
    mocks["mark_earnings_accrued"].assert_called_once_with([1], 6)
    client.get_receipt.assert_not_called()


@pytest.mark.asyncio
async def test_reconcile_fails_and_reverts_when_receipt_never_found(sb):
    """Stuck long enough with a tx_hash but no receipt ever surfaces --
    treated as failed (very likely dropped on a ~2s-block chain), not left
    pending indefinitely."""
    stuck = {"id": 7, "tx_hash": "0xdeadbeef", "provider_id": 1}
    stack, mocks = _patched_reconcile(list_stuck_pending_settlements=[stuck])
    with stack:
        client = _client(receipt=None)
        result = await reconcile_stuck_settlements(client)

    assert result.settlements_marked_failed == 1
    mocks["mark_earnings_accrued"].assert_called_once_with([1], 7)


@pytest.mark.asyncio
async def test_reconcile_is_a_noop_when_nothing_is_stuck(sb):
    stack, mocks = _patched_reconcile(list_stuck_pending_settlements=[])
    with stack:
        client = _client()
        result = await reconcile_stuck_settlements(client)

    assert result.settlements_checked == 0
    mocks["mark_settlement_sent"].assert_not_called()
    mocks["mark_settlement_failed"].assert_not_called()
