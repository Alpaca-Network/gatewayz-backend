"""The anti-farm rule, tested end to end across the sweep and the accrual.

Every other test in this package exercises one layer against a fake of the
other. This one runs the real observation sweep several times against one
in-memory store and then runs the real accrual over what the sweep wrote,
because the farm being defended against lives in the seam between them: a
sweep that records nothing is not the same as a sweep that records zero,
and only the accrual reading the sweep's own output can tell the
difference.

The exploit: fund a wallet just before two of the four scheduled sweeps and
keep it empty the rest of the day. If an empty sweep leaves no trace, the
only readings that exist are the funded ones, the minimum is taken across
those alone, and the wallet is paid as though it held that balance all day
-- which is exactly what the module docstring promises cannot happen.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

import src.services.holdings.rewards as rewards
import src.services.holdings.snapshots as snapshots
from src.services.holdings.chains import BalanceReading, BalanceReadResult, ChainReadFailure
from src.services.holdings.prices import PricePoint

DAY = date(2026, 9, 14)
DAY_STR = "2026-09-14"
WALLET = "0x" + "a" * 40

TOKEN_ROW = {
    "id": 1,
    "chain_id": 1,
    "contract_address": "0x" + "c" * 40,
    "decimals": 6,
    "symbol": "USDC",
    "price_id": "usd-coin",
    "is_enabled": True,
}

SWEEP_HOURS = (0, 6, 12, 18)


class Store:
    """One in-memory stand-in for both holdings tables plus the wallet and
    credit lookups, shared by the sweep and the accrual so what one writes
    is what the other reads."""

    def __init__(self):
        self.snapshot_rows: list[dict] = []
        self.sweep_rows: dict[tuple[str, str], Decimal] = {}
        self.accruals: dict[tuple[str, str], dict] = {}
        self.credit_calls: list[dict] = []
        self._next_id = 1
        self.balance_by_sweep: dict[str, int] = {}

    # -- written by the sweep --------------------------------------------
    def record_snapshot(self, wallet_address, token_id, raw_amount, usd_value, taken_at):
        row = {
            "wallet_address": wallet_address.lower(),
            "token_id": token_id,
            "raw_amount": raw_amount,
            "usd_value": usd_value,
            "taken_at": taken_at.isoformat(),
        }
        self.snapshot_rows.append(row)
        return dict(row, id=len(self.snapshot_rows))

    def record_sweep(self, wallet_address, taken_at, usd_total):
        key = (wallet_address.lower(), taken_at.isoformat())
        self.sweep_rows[key] = Decimal(str(usd_total))
        return {
            "id": len(self.sweep_rows),
            "wallet_address": key[0],
            "taken_at": key[1],
            "usd_total": str(usd_total),
        }

    # -- read by the accrual ---------------------------------------------
    def get_sweep_totals_for_date(self, wallet_address, day):
        totals = {
            taken_at: value
            for (wallet, taken_at), value in self.sweep_rows.items()
            if wallet == wallet_address.lower() and taken_at.startswith(day.isoformat())
        }
        return totals or None

    def list_wallets_with_sweeps_for_date(self, day):
        return sorted(
            {
                wallet
                for (wallet, taken_at) in self.sweep_rows
                if taken_at.startswith(day.isoformat())
            }
        )

    # -- accrual bookkeeping ---------------------------------------------
    def get_active_holdings_rates(self):
        return [{"id": 1, "min_usd": "0", "credits_per_1k_usd_per_day": "1.000000"}]

    def get_holdings_accrual(self, wallet_address, reward_date):
        day = reward_date if isinstance(reward_date, str) else reward_date.isoformat()
        row = self.accruals.get((wallet_address.lower(), day))
        return dict(row) if row else None

    def create_holdings_accrual(self, wallet_address, reward_date, usd_basis, credits):
        day = reward_date if isinstance(reward_date, str) else reward_date.isoformat()
        row = {
            "id": self._next_id,
            "wallet_address": wallet_address.lower(),
            "reward_date": day,
            "usd_basis": str(usd_basis),
            "credits": str(credits),
            "status": "pending",
            "ledger_request_id": None,
        }
        self._next_id += 1
        self.accruals[(row["wallet_address"], day)] = row
        return dict(row)

    def mark_holdings_accrual_paid(self, accrual_id, ledger_request_id):
        for row in self.accruals.values():
            if row["id"] == accrual_id:
                row["status"] = "paid"
                row["ledger_request_id"] = ledger_request_id
                return dict(row)
        return None

    def list_pending_holdings_accruals(self, wallet_address):
        return []

    def list_pending_holdings_accruals_since(self, min_reward_date):
        return []

    def get_wallet(self, address):
        return {"wallet_address": address.lower(), "user_id": 7}

    def get_wallets_for_user(self, user_id):
        return [{"wallet_address": WALLET, "user_id": 7}]

    def add_credits_to_user(self, **kwargs):
        self.credit_calls.append(kwargs)


@pytest.fixture
def store(monkeypatch):
    s = Store()

    # -- the sweep --------------------------------------------------------
    monkeypatch.setattr(snapshots.Config, "HOLDINGS_REWARDS_ENABLED", True, raising=False)
    monkeypatch.setattr(snapshots.Config, "HOLDINGS_MIN_WALLET_AGE_DAYS", 3, raising=False)
    monkeypatch.setattr(snapshots, "list_enabled_tokens", lambda: [TOKEN_ROW])
    monkeypatch.setattr(
        snapshots,
        "list_all_wallets",
        lambda: [{"wallet_address": WALLET, "created_at": "2026-01-01T00:00:00+00:00"}],
    )
    monkeypatch.setattr(
        snapshots,
        "get_usd_prices",
        lambda ids: {"usd-coin": PricePoint(price=Decimal("1"), as_of=datetime.now(UTC))},
    )
    monkeypatch.setattr(snapshots, "record_snapshot", s.record_snapshot)
    monkeypatch.setattr(snapshots, "record_sweep", s.record_sweep, raising=False)

    def fake_read(address, tokens):
        raw = s.balance_by_sweep["current"]
        if raw == "unreadable":
            return BalanceReadResult(
                readings=[], failures=[ChainReadFailure(chain_id=1, reason="timeout")]
            )
        (token,) = tokens
        return BalanceReadResult(
            readings=[BalanceReading(token=token, raw_amount=raw)], failures=[]
        )

    monkeypatch.setattr(snapshots, "read_balances", fake_read)

    # -- the accrual ------------------------------------------------------
    monkeypatch.setattr(rewards.Config, "HOLDINGS_REWARDS_ENABLED", True, raising=False)
    monkeypatch.setattr(rewards.Config, "HOLDINGS_DAILY_CAP_CREDITS", 50.0, raising=False)
    monkeypatch.setattr(
        rewards.Config, "HOLDINGS_GLOBAL_DAILY_BUDGET_CREDITS", 10000.0, raising=False
    )
    monkeypatch.setattr(rewards.Config, "HOLDINGS_SNAPSHOTS_PER_DAY", 4, raising=False)
    monkeypatch.setattr(rewards.Config, "HOLDINGS_MIN_SNAPSHOT_BATCHES_PER_DAY", 2, raising=False)
    # The usage match is a separate ceiling, tested in test_rewards.py. It is
    # off here so that what this file asserts about the basis is the basis and
    # not a spend limit quietly clipping the same number.
    monkeypatch.setattr(rewards.Config, "HOLDINGS_USAGE_MATCH_ENABLED", False, raising=False)
    for name, attr in (
        ("get_sweep_totals_for_date", s.get_sweep_totals_for_date),
        ("list_wallets_with_sweeps_for_date", s.list_wallets_with_sweeps_for_date),
        ("get_active_holdings_rates", s.get_active_holdings_rates),
        ("get_holdings_accrual", s.get_holdings_accrual),
        ("create_holdings_accrual", s.create_holdings_accrual),
        ("mark_holdings_accrual_paid", s.mark_holdings_accrual_paid),
        ("list_pending_holdings_accruals", s.list_pending_holdings_accruals),
        ("list_pending_holdings_accruals_since", s.list_pending_holdings_accruals_since),
        ("get_wallet", s.get_wallet),
        ("get_wallets_for_user", s.get_wallets_for_user),
        ("add_credits_to_user", s.add_credits_to_user),
    ):
        monkeypatch.setattr(rewards, name, attr, raising=False)

    return s


def _run_day(store: Store, balances: list) -> None:
    """Run one sweep per scheduled hour, with `balances[i]` the wallet's raw
    balance at sweep i. "unreadable" makes that sweep's chain read fail."""
    for hour, raw in zip(SWEEP_HOURS, balances, strict=True):
        store.balance_by_sweep["current"] = raw
        snapshots.run_holdings_snapshots_once(
            now=datetime(DAY.year, DAY.month, DAY.day, hour, 5, tzinfo=UTC)
        )


FUNDED = 10_000_000_000  # 10,000 USDC at 6 decimals -> $10,000
EMPTY = 0


class TestEmptySweepsAreObserved:
    def test_a_wallet_emptied_between_sweeps_earns_nothing(self, store):
        """THE EXPLOIT. Funded for two of the four sweeps, empty for the
        other two. Paying on the lowest value SEEN means the empty sweeps
        must count -- otherwise funding a wallet for twenty minutes a day
        buys a full day of credits."""
        _run_day(store, [FUNDED, EMPTY, FUNDED, EMPTY])

        result = rewards.run_holdings_rewards_once(DAY)

        assert store.credit_calls == []
        assert store.accruals == {}
        assert result["paid"] == 0

    def test_an_empty_sweep_is_recorded_as_a_zero_total(self, store):
        _run_day(store, [EMPTY, EMPTY, EMPTY, EMPTY])
        assert len(store.sweep_rows) == 4
        assert set(store.sweep_rows.values()) == {Decimal(0)}
        assert store.snapshot_rows == []

    def test_a_wallet_funded_all_day_is_still_paid(self, store):
        """The fix must not make the feature pay nobody."""
        _run_day(store, [FUNDED, FUNDED, FUNDED, FUNDED])
        result = rewards.run_holdings_rewards_once(DAY)
        assert result["paid"] == 1
        assert store.accruals[(WALLET, DAY_STR)]["usd_basis"] == "10000.000000000000000000"

    def test_the_basis_is_the_thinnest_sweep_of_the_day(self, store):
        _run_day(store, [FUNDED, FUNDED // 4, FUNDED, FUNDED])
        rewards.run_holdings_rewards_once(DAY)
        assert store.accruals[(WALLET, DAY_STR)]["usd_basis"] == "2500.000000000000000000"


class TestUnmeasurableSweepsStillRecordNothing:
    """ "We could not measure it" is not "they held zero". An unreadable
    chain must leave no trace at all, or an RPC outage would silently
    rewrite every holder's day to nothing."""

    def test_an_incomplete_chain_read_records_no_sweep_row(self, store):
        _run_day(store, [FUNDED, "unreadable", FUNDED, FUNDED])
        assert len(store.sweep_rows) == 3
        assert Decimal(0) not in store.sweep_rows.values()

    def test_an_outage_does_not_zero_out_a_funded_day(self, store):
        _run_day(store, [FUNDED, "unreadable", "unreadable", FUNDED])
        result = rewards.run_holdings_rewards_once(DAY)
        assert result["paid"] == 1
        assert store.accruals[(WALLET, DAY_STR)]["usd_basis"] == "10000.000000000000000000"

    def test_a_day_of_nothing_but_outages_is_not_paid_at_all(self, store):
        """No sweeps at all means the observation job did not run, which is
        an operator-visible failure rather than a silent zero payout."""
        _run_day(store, ["unreadable"] * 4)
        with pytest.raises(rewards.HoldingsSnapshotsMissingError):
            rewards.run_holdings_rewards_once(DAY)
        assert store.credit_calls == []

    def test_one_readable_sweep_is_too_few_to_pay(self, store):
        _run_day(store, [FUNDED, "unreadable", "unreadable", "unreadable"])
        result = rewards.run_holdings_rewards_once(DAY)
        assert result["skipped"]["too_few_batches"] == 1
        assert store.credit_calls == []


class TestPerTokenDetailIsStillRecorded:
    def test_a_funded_sweep_writes_both_the_sweep_total_and_its_token_rows(self, store):
        _run_day(store, [FUNDED, FUNDED, FUNDED, FUNDED])
        assert len(store.sweep_rows) == 4
        assert len(store.snapshot_rows) == 4
        assert all(r["token_id"] == 1 for r in store.snapshot_rows)

    def test_every_token_row_has_a_sweep_row_for_the_same_moment(self, store):
        _run_day(store, [FUNDED, EMPTY, FUNDED, EMPTY])
        for row in store.snapshot_rows:
            assert (row["wallet_address"], row["taken_at"]) in store.sweep_rows


def test_sweep_totals_span_only_the_requested_day(store):
    _run_day(store, [FUNDED, FUNDED, FUNDED, FUNDED])
    other = DAY + timedelta(days=1)
    assert store.get_sweep_totals_for_date(WALLET, other) is None
