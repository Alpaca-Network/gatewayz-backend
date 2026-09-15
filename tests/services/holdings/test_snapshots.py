"""Tests for src.services.holdings.snapshots -- the sweep that observes
wallet balances for holdings rewards.

The two rules under test that are about correctness rather than cost: an
incomplete chain read and a held-but-unpriced token each cause the WHOLE
batch for that wallet to be dropped. Recording a partial set would produce
an artificially low total which, under the lowest-of-day payout rule, would
silently underpay the holder.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

import src.services.holdings.snapshots as snapshots
from src.services.holdings.chains import (
    BalanceReading,
    BalanceReadResult,
    ChainReadFailure,
    InvalidWalletAddressError,
    TokenRef,
)
from src.services.holdings.prices import PricePoint

WALLET = "0x" + "a" * 40
OTHER_WALLET = "0x" + "b" * 40

NOW = datetime(2026, 9, 15, 0, 5, tzinfo=UTC)

WETH_ROW = {
    "id": 1,
    "chain_id": 1,
    "contract_address": "0x" + "c" * 40,
    "decimals": 18,
    "symbol": "WETH",
    "price_id": "weth",
    "is_enabled": True,
}
USDC_ROW = {
    "id": 2,
    "chain_id": 1,
    "contract_address": "0x" + "d" * 40,
    "decimals": 6,
    "symbol": "USDC",
    "price_id": "usd-coin",
    "is_enabled": True,
}


def _wallet_row(address: str, age_days: int = 30) -> dict:
    return {
        "id": 1,
        "user_id": 7,
        "wallet_address": address,
        "created_at": (NOW - timedelta(days=age_days)).isoformat(),
    }


def _price(value: str) -> PricePoint:
    return PricePoint(price=Decimal(value), as_of=NOW)


class SweepRecorder:
    """Captures record_sweep() calls -- one per fully measured wallet-sweep,
    zero total included."""

    def __init__(self):
        self.rows: list[dict] = []
        self.fail = False

    def __call__(self, wallet_address, taken_at, usd_total):
        if self.fail:
            return None
        row = {
            "wallet_address": wallet_address,
            "taken_at": taken_at,
            "usd_total": usd_total,
        }
        self.rows.append(row)
        return dict(row, id=len(self.rows))


class Recorder:
    """Captures record_snapshot() calls (the per-token detail)."""

    def __init__(self):
        self.rows: list[dict] = []

    def __call__(self, wallet_address, token_id, raw_amount, usd_value, taken_at):
        row = {
            "wallet_address": wallet_address,
            "token_id": token_id,
            "raw_amount": raw_amount,
            "usd_value": usd_value,
            "taken_at": taken_at,
        }
        self.rows.append(row)
        return dict(row, id=len(self.rows))


@pytest.fixture
def wired(monkeypatch):
    """Wire the module under test to in-memory doubles and return a small
    control object each test tweaks."""

    class Wiring:
        def __init__(self):
            self.tokens = [WETH_ROW, USDC_ROW]
            self.wallets = [_wallet_row(WALLET)]
            self.prices: dict[str, PricePoint] = {
                "weth": _price("2000"),
                "usd-coin": _price("1"),
            }
            self.balances: dict[str, BalanceReadResult] = {}
            self.recorder = Recorder()
            self.sweeps = SweepRecorder()
            self.enabled = True

    w = Wiring()

    monkeypatch.setattr(snapshots.Config, "HOLDINGS_REWARDS_ENABLED", True, raising=False)
    monkeypatch.setattr(snapshots.Config, "HOLDINGS_MIN_WALLET_AGE_DAYS", 3, raising=False)
    monkeypatch.setattr(snapshots, "list_enabled_tokens", lambda: list(w.tokens))
    monkeypatch.setattr(snapshots, "list_all_wallets", lambda: list(w.wallets))
    monkeypatch.setattr(snapshots, "get_usd_prices", lambda ids: dict(w.prices))
    monkeypatch.setattr(snapshots, "record_snapshot", w.recorder)
    monkeypatch.setattr(snapshots, "record_sweep", w.sweeps)

    def fake_read(address, tokens):
        result = w.balances.get(address.lower())
        if result is None:
            raise AssertionError(f"no balances configured for {address}")
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(snapshots, "read_balances", fake_read)
    return w


def _refs() -> list[TokenRef]:
    return snapshots.token_refs_from_rows([WETH_ROW, USDC_ROW])


def _reading(row: dict, raw_amount: int) -> BalanceReading:
    ref = next(r for r in _refs() if r.symbol == row["symbol"])
    return BalanceReading(token=ref, raw_amount=raw_amount)


class TestGating:
    def test_disabled_flag_no_ops(self, wired, monkeypatch):
        monkeypatch.setattr(snapshots.Config, "HOLDINGS_REWARDS_ENABLED", False, raising=False)
        monkeypatch.setattr(
            snapshots,
            "list_enabled_tokens",
            lambda: pytest.fail("must not touch the registry while disabled"),
        )
        assert snapshots.run_holdings_snapshots_once(now=NOW) == {"skipped": "disabled"}

    def test_empty_registry_no_ops(self, wired):
        wired.tokens = []
        result = snapshots.run_holdings_snapshots_once(now=NOW)
        assert result == {"skipped": "no_tokens"}
        assert wired.recorder.rows == []

    def test_no_wallets_records_nothing(self, wired):
        wired.wallets = []
        result = snapshots.run_holdings_snapshots_once(now=NOW)
        assert result["wallets_considered"] == 0
        assert result["rows_recorded"] == 0


class TestWalletAge:
    def test_wallet_younger_than_minimum_is_not_read(self, wired):
        wired.wallets = [_wallet_row(WALLET, age_days=1)]
        # No balances configured -- reading it at all would raise.
        result = snapshots.run_holdings_snapshots_once(now=NOW)
        assert result["wallets_considered"] == 0
        assert result["skipped"]["too_new"] == 1
        assert wired.recorder.rows == []

    def test_wallet_exactly_at_minimum_age_is_read(self, wired):
        wired.wallets = [_wallet_row(WALLET, age_days=3)]
        wired.balances[WALLET] = BalanceReadResult(
            readings=[_reading(USDC_ROW, 5_000_000)], failures=[]
        )
        result = snapshots.run_holdings_snapshots_once(now=NOW)
        assert result["wallets_considered"] == 1
        assert result["rows_recorded"] == 1

    def test_unparseable_created_at_is_skipped_not_read(self, wired):
        row = _wallet_row(WALLET)
        row["created_at"] = "not-a-timestamp"
        wired.wallets = [row]
        result = snapshots.run_holdings_snapshots_once(now=NOW)
        assert result["skipped"]["unknown_age"] == 1
        assert wired.recorder.rows == []


class TestHappyPath:
    def test_records_one_row_per_nonzero_token_sharing_one_taken_at(self, wired):
        wired.balances[WALLET] = BalanceReadResult(
            readings=[
                _reading(WETH_ROW, 2 * 10**18),  # 2 WETH @ 2000 = 4000
                _reading(USDC_ROW, 250_000_000),  # 250 USDC @ 1 = 250
            ],
            failures=[],
        )
        result = snapshots.run_holdings_snapshots_once(now=NOW)

        assert result["rows_recorded"] == 2
        assert result["wallets_recorded"] == 1
        assert {r["token_id"] for r in wired.recorder.rows} == {1, 2}
        assert len({r["taken_at"] for r in wired.recorder.rows}) == 1
        by_token = {r["token_id"]: r for r in wired.recorder.rows}
        assert by_token[1]["usd_value"] == Decimal("4000")
        assert by_token[2]["usd_value"] == Decimal("250")
        assert by_token[1]["raw_amount"] == 2 * 10**18

    def test_every_wallet_in_one_sweep_shares_one_taken_at(self, wired):
        wired.wallets = [_wallet_row(WALLET), _wallet_row(OTHER_WALLET)]
        for address in (WALLET, OTHER_WALLET):
            wired.balances[address] = BalanceReadResult(
                readings=[_reading(USDC_ROW, 1_000_000)], failures=[]
            )
        snapshots.run_holdings_snapshots_once(now=NOW)
        assert len({r["taken_at"] for r in wired.recorder.rows}) == 1

    def test_zero_balance_token_is_not_recorded(self, wired):
        wired.balances[WALLET] = BalanceReadResult(
            readings=[_reading(WETH_ROW, 0), _reading(USDC_ROW, 1_000_000)],
            failures=[],
        )
        snapshots.run_holdings_snapshots_once(now=NOW)
        assert [r["token_id"] for r in wired.recorder.rows] == [2]

    def test_wallet_holding_nothing_records_a_zero_sweep_and_no_token_rows(self, wired):
        """An empty wallet must still leave a trace. Per-token rows cannot
        carry it -- there are none -- so the sweep row does, with a total of
        zero, which is what drags the day's minimum down for a wallet that
        was emptied between sweeps."""
        wired.balances[WALLET] = BalanceReadResult(
            readings=[_reading(WETH_ROW, 0), _reading(USDC_ROW, 0)], failures=[]
        )
        result = snapshots.run_holdings_snapshots_once(now=NOW)
        assert wired.recorder.rows == []
        assert [r["usd_total"] for r in wired.sweeps.rows] == [Decimal(0)]
        assert result["wallets_empty"] == 1
        assert result["sweeps_recorded"] == 1
        assert result["wallets_recorded"] == 0

    def test_prices_are_fetched_once_for_the_whole_sweep(self, wired, monkeypatch):
        calls = []

        def counting_prices(ids):
            calls.append(list(ids))
            return dict(wired.prices)

        monkeypatch.setattr(snapshots, "get_usd_prices", counting_prices)
        wired.wallets = [_wallet_row(WALLET), _wallet_row(OTHER_WALLET)]
        for address in (WALLET, OTHER_WALLET):
            wired.balances[address] = BalanceReadResult(
                readings=[_reading(USDC_ROW, 1_000_000)], failures=[]
            )
        snapshots.run_holdings_snapshots_once(now=NOW)
        assert len(calls) == 1
        assert sorted(calls[0]) == ["usd-coin", "weth"]


class TestIncompleteReadDropsBatch:
    def test_partial_chain_read_records_nothing_for_that_wallet(self, wired):
        wired.balances[WALLET] = BalanceReadResult(
            readings=[_reading(USDC_ROW, 250_000_000)],
            failures=[ChainReadFailure(chain_id=8453, reason="timeout")],
        )
        result = snapshots.run_holdings_snapshots_once(now=NOW)
        assert wired.recorder.rows == []
        assert result["skipped"]["incomplete_read"] == 1
        assert result["wallets_recorded"] == 0

    def test_one_incomplete_wallet_does_not_stop_the_sweep(self, wired):
        wired.wallets = [_wallet_row(WALLET), _wallet_row(OTHER_WALLET)]
        wired.balances[WALLET] = BalanceReadResult(
            readings=[], failures=[ChainReadFailure(chain_id=1, reason="timeout")]
        )
        wired.balances[OTHER_WALLET] = BalanceReadResult(
            readings=[_reading(USDC_ROW, 1_000_000)], failures=[]
        )
        result = snapshots.run_holdings_snapshots_once(now=NOW)
        assert [r["wallet_address"] for r in wired.recorder.rows] == [OTHER_WALLET]
        assert result["skipped"]["incomplete_read"] == 1
        assert result["wallets_recorded"] == 1


class TestMissingPriceDropsBatch:
    def test_held_token_without_a_fresh_price_drops_the_whole_batch(self, wired):
        wired.prices = {"usd-coin": _price("1")}  # no weth price
        wired.balances[WALLET] = BalanceReadResult(
            readings=[
                _reading(WETH_ROW, 2 * 10**18),
                _reading(USDC_ROW, 250_000_000),
            ],
            failures=[],
        )
        result = snapshots.run_holdings_snapshots_once(now=NOW)
        assert wired.recorder.rows == []
        assert result["skipped"]["missing_price"] == 1

    def test_zero_balance_token_without_a_price_does_not_block_the_batch(self, wired):
        wired.prices = {"usd-coin": _price("1")}  # no weth price
        wired.balances[WALLET] = BalanceReadResult(
            readings=[_reading(WETH_ROW, 0), _reading(USDC_ROW, 250_000_000)],
            failures=[],
        )
        result = snapshots.run_holdings_snapshots_once(now=NOW)
        assert [r["token_id"] for r in wired.recorder.rows] == [2]
        assert result["skipped"]["missing_price"] == 0
        assert result["wallets_recorded"] == 1

    def test_non_positive_price_is_treated_as_missing(self, wired):
        wired.prices = {"usd-coin": _price("1"), "weth": _price("0")}
        wired.balances[WALLET] = BalanceReadResult(
            readings=[_reading(WETH_ROW, 10**18), _reading(USDC_ROW, 1_000_000)],
            failures=[],
        )
        result = snapshots.run_holdings_snapshots_once(now=NOW)
        assert wired.recorder.rows == []
        assert result["skipped"]["missing_price"] == 1


class TestFailureIsolation:
    def test_invalid_address_does_not_abort_the_sweep(self, wired):
        wired.wallets = [_wallet_row("not-an-address"), _wallet_row(OTHER_WALLET)]
        wired.balances["not-an-address"] = InvalidWalletAddressError("bad")
        wired.balances[OTHER_WALLET] = BalanceReadResult(
            readings=[_reading(USDC_ROW, 1_000_000)], failures=[]
        )
        result = snapshots.run_holdings_snapshots_once(now=NOW)
        assert result["skipped"]["error"] == 1
        assert result["wallets_recorded"] == 1

    def test_failed_insert_is_counted_but_does_not_raise(self, wired, monkeypatch):
        monkeypatch.setattr(snapshots, "record_snapshot", lambda *a, **k: None)
        wired.balances[WALLET] = BalanceReadResult(
            readings=[_reading(USDC_ROW, 1_000_000)], failures=[]
        )
        result = snapshots.run_holdings_snapshots_once(now=NOW)
        assert result["rows_recorded"] == 0
        assert result["rows_failed"] == 1


class TestTokenRefsFromRows:
    def test_maps_registry_rows_to_token_refs(self):
        refs = snapshots.token_refs_from_rows([WETH_ROW, USDC_ROW])
        assert [r.symbol for r in refs] == ["WETH", "USDC"]
        assert refs[0].chain_id == 1
        assert refs[0].contract_address == WETH_ROW["contract_address"]
        assert refs[1].decimals == 6

    def test_null_contract_address_means_the_native_asset(self):
        native = dict(WETH_ROW, contract_address=None, symbol="ETH", price_id="ethereum")
        (ref,) = snapshots.token_refs_from_rows([native])
        assert ref.is_native is True

    def test_malformed_row_is_dropped_not_fatal(self):
        refs = snapshots.token_refs_from_rows([{"id": 9, "symbol": "BROKEN"}, USDC_ROW])
        assert [r.symbol for r in refs] == ["USDC"]


class TestSweepRows:
    """One sweep row per fully measured wallet-sweep. It is the unit the
    payout reads, so what it does and does not record is the anti-farm rule."""

    def test_a_funded_sweep_records_the_total_alongside_the_token_rows(self, wired):
        wired.balances[WALLET] = BalanceReadResult(
            readings=[
                _reading(WETH_ROW, 2 * 10**18),  # $4000
                _reading(USDC_ROW, 250_000_000),  # $250
            ],
            failures=[],
        )
        result = snapshots.run_holdings_snapshots_once(now=NOW)
        assert [r["usd_total"] for r in wired.sweeps.rows] == [Decimal("4250")]
        assert result["sweeps_recorded"] == 1
        assert result["rows_recorded"] == 2

    def test_the_sweep_row_shares_taken_at_with_its_token_rows(self, wired):
        wired.balances[WALLET] = BalanceReadResult(
            readings=[_reading(USDC_ROW, 1_000_000)], failures=[]
        )
        snapshots.run_holdings_snapshots_once(now=NOW)
        assert {r["taken_at"] for r in wired.sweeps.rows} == {
            r["taken_at"] for r in wired.recorder.rows
        }

    def test_an_incomplete_read_records_no_sweep_row(self, wired):
        """ "We could not measure it" must never be written down as "they
        held zero" -- an RPC outage would otherwise zero out every holder."""
        wired.balances[WALLET] = BalanceReadResult(
            readings=[_reading(USDC_ROW, 250_000_000)],
            failures=[ChainReadFailure(chain_id=8453, reason="timeout")],
        )
        result = snapshots.run_holdings_snapshots_once(now=NOW)
        assert wired.sweeps.rows == []
        assert result["sweeps_recorded"] == 0

    def test_a_held_token_without_a_price_records_no_sweep_row(self, wired):
        wired.prices = {"usd-coin": _price("1")}
        wired.balances[WALLET] = BalanceReadResult(
            readings=[_reading(WETH_ROW, 2 * 10**18), _reading(USDC_ROW, 250_000_000)],
            failures=[],
        )
        result = snapshots.run_holdings_snapshots_once(now=NOW)
        assert wired.sweeps.rows == []
        assert result["sweeps_recorded"] == 0

    def test_a_failed_sweep_write_suppresses_the_token_rows_too(self, wired):
        """Detail must never outlive the sweep it belongs to, or the audit
        trail shows holdings for a moment the payout cannot see."""
        wired.sweeps.fail = True
        wired.balances[WALLET] = BalanceReadResult(
            readings=[_reading(USDC_ROW, 250_000_000)], failures=[]
        )
        result = snapshots.run_holdings_snapshots_once(now=NOW)
        assert wired.recorder.rows == []
        assert result["skipped"]["sweep_write_failed"] == 1
        assert result["rows_recorded"] == 0

    def test_every_wallet_in_one_sweep_gets_its_own_row(self, wired):
        wired.wallets = [_wallet_row(WALLET), _wallet_row(OTHER_WALLET)]
        wired.balances[WALLET] = BalanceReadResult(
            readings=[_reading(USDC_ROW, 1_000_000)], failures=[]
        )
        wired.balances[OTHER_WALLET] = BalanceReadResult(
            readings=[_reading(USDC_ROW, 0)], failures=[]
        )
        result = snapshots.run_holdings_snapshots_once(now=NOW)
        assert len(wired.sweeps.rows) == 2
        assert result["sweeps_recorded"] == 2
