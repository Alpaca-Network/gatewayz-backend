"""Tests for src.services.holdings.rewards -- the daily accrual that pays
inference credits for tokens a user holds in a wallet they proved.

The invariants under test: the basis is the day's LOWEST observed value
(never an average, never the latest), the per-account cap and the global
run budget are both hard ceilings, and the whole thing is idempotent twice
over -- once on the accrual's unique (wallet, date) index and once on the
credit ledger's request_id.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

import src.services.holdings.rewards as rewards
from src.services.holdings.rewards import HoldingsSnapshotsMissingError

DAY = date(2026, 9, 14)
DAY_STR = "2026-09-14"

W1 = "0x" + "1" * 40
W2 = "0x" + "2" * 40
W3 = "0x" + "3" * 40


class FakeHoldingsDB:
    """In-memory stand-in for src.db.holdings + src.db.user_wallets +
    src.db.users, wired into the module under test by monkeypatch. Lets one
    test drive a realistic multi-run scenario (a re-run, a wallet linked
    between runs) without touching Supabase.
    """

    def __init__(self):
        self.rates = [
            {"id": 1, "min_usd": "0", "credits_per_1k_usd_per_day": "1.000000"},
            {"id": 2, "min_usd": "1000", "credits_per_1k_usd_per_day": "2.000000"},
        ]
        # (wallet, day) -> {taken_at: sweep total}. These stand in for
        # wallet_holdings_sweeps rows: several sweeps a day is the normal
        # case, one sweep is the farmable case the job refuses.
        self.batches: dict[tuple[str, str], dict[str, Decimal] | None] = {}
        self.accruals: dict[tuple[str, str], dict] = {}
        self._next_id = 1
        self.linked: dict[str, dict] = {}
        self.credit_calls: list[dict] = []
        self.fail_next_credit_calls = 0

    # -- src.db.holdings ------------------------------------------------------
    def get_active_holdings_rates(self):
        return list(self.rates)

    def list_wallets_with_sweeps_for_date(self, day):
        return sorted(
            {w for (w, d) in self.batches if d == day.isoformat()},
        )

    def get_sweep_totals_for_date(self, wallet_address, day):
        return self.batches.get((wallet_address.lower(), day.isoformat()))

    def get_holdings_accrual(self, wallet_address, reward_date):
        key = (wallet_address.lower(), _day_str(reward_date))
        row = self.accruals.get(key)
        return dict(row) if row else None

    def create_holdings_accrual(self, wallet_address, reward_date, usd_basis, credits):
        key = (wallet_address.lower(), _day_str(reward_date))
        if key in self.accruals:
            return None
        row = {
            "id": self._next_id,
            "wallet_address": wallet_address.lower(),
            "reward_date": _day_str(reward_date),
            "usd_basis": str(usd_basis),
            "credits": str(credits),
            "status": "pending",
            "ledger_request_id": None,
        }
        self._next_id += 1
        self.accruals[key] = row
        return dict(row)

    def mark_holdings_accrual_paid(self, accrual_id, ledger_request_id):
        for row in self.accruals.values():
            if row["id"] == accrual_id:
                row["status"] = "paid"
                row["ledger_request_id"] = ledger_request_id
                return dict(row)
        return None

    def list_pending_holdings_accruals(self, wallet_address):
        return [
            dict(r)
            for r in self.accruals.values()
            if r["wallet_address"] == wallet_address.lower() and r["status"] == "pending"
        ]

    def list_pending_holdings_accruals_since(self, min_reward_date):
        return sorted(
            (dict(r) for r in self.accruals.values() if r["status"] == "pending"),
            key=lambda r: (r["reward_date"], r["wallet_address"]),
        )

    # -- src.db.user_wallets --------------------------------------------------
    def get_wallet(self, address):
        row = self.linked.get(address.lower())
        return dict(row) if row else None

    def get_wallets_for_user(self, user_id):
        return [dict(r) for r in self.linked.values() if r["user_id"] == user_id]

    # -- src.db.users ---------------------------------------------------------
    def add_credits_to_user(self, **kwargs):
        if self.fail_next_credit_calls > 0:
            self.fail_next_credit_calls -= 1
            raise RuntimeError("ledger write failed")
        self.credit_calls.append(kwargs)

    def link(self, address, user_id):
        self.linked[address.lower()] = {"wallet_address": address.lower(), "user_id": user_id}

    def observe(self, address, usd, day=DAY, batches=2):
        """Record `batches` completed sweeps for the day whose LOWEST total
        is `usd`.

        The extra sweeps are worth more, so the minimum is unambiguously the
        one asserted on -- a test that passes because every sweep is equal
        would not prove the job takes the minimum.
        """
        low = Decimal(str(usd))
        self.batches[(address.lower(), day.isoformat())] = {
            f"2026-09-14T{h:02d}:00:00+00:00": low if i == 0 else low * 10
            for i, h in enumerate(range(0, 24, max(1, 24 // max(batches, 1)))[:batches])
        }


def _day_str(value):
    return value if isinstance(value, str) else value.isoformat()


@pytest.fixture
def db(monkeypatch):
    fake = FakeHoldingsDB()
    monkeypatch.setattr(rewards.Config, "HOLDINGS_REWARDS_ENABLED", True, raising=False)
    monkeypatch.setattr(rewards.Config, "HOLDINGS_DAILY_CAP_CREDITS", 50.0, raising=False)
    monkeypatch.setattr(rewards.Config, "HOLDINGS_SNAPSHOTS_PER_DAY", 4, raising=False)
    monkeypatch.setattr(rewards.Config, "HOLDINGS_MIN_SNAPSHOT_BATCHES_PER_DAY", 2, raising=False)
    monkeypatch.setattr(
        rewards.Config, "HOLDINGS_GLOBAL_DAILY_BUDGET_CREDITS", 10000.0, raising=False
    )
    for name in (
        "get_active_holdings_rates",
        "list_wallets_with_sweeps_for_date",
        "get_sweep_totals_for_date",
        "get_holdings_accrual",
        "create_holdings_accrual",
        "mark_holdings_accrual_paid",
        "list_pending_holdings_accruals",
        "list_pending_holdings_accruals_since",
        "get_wallet",
        "get_wallets_for_user",
        "add_credits_to_user",
    ):
        monkeypatch.setattr(rewards, name, getattr(fake, name))
    return fake


class TestGating:
    def test_disabled_flag_no_ops(self, db, monkeypatch):
        monkeypatch.setattr(rewards.Config, "HOLDINGS_REWARDS_ENABLED", False, raising=False)
        db.observe(W1, 1000)
        db.link(W1, 7)
        assert rewards.run_holdings_rewards_once(DAY) == {"skipped": "disabled"}
        assert db.credit_calls == []
        assert db.accruals == {}

    def test_no_snapshots_for_the_date_raises(self, db):
        with pytest.raises(HoldingsSnapshotsMissingError):
            rewards.run_holdings_rewards_once(DAY)

    def test_defaults_to_yesterday(self, db, monkeypatch):
        yesterday = date(2026, 9, 14)
        db.observe(W1, 1000, day=yesterday)
        db.link(W1, 7)
        result = rewards.run_holdings_rewards_once(None)
        # FakeHoldingsDB only holds snapshots for 2026-09-14, so a run that
        # found them proves the default date was yesterday relative to the
        # frozen clock below.
        assert result["reward_date"] == yesterday.isoformat()

    @pytest.fixture(autouse=True)
    def _freeze_today(self, monkeypatch):
        monkeypatch.setattr(rewards, "_today", lambda: date(2026, 9, 15))


class TestBasisAndRate:
    def test_pays_on_the_lowest_value_of_the_day(self, db):
        # 400 USD at the thinnest point -> tier 1 -> 400/1000 * 1 = 0.4
        db.observe(W1, 400)
        db.link(W1, 7)
        result = rewards.run_holdings_rewards_once(DAY)
        assert result["paid"] == 1
        assert db.accruals[(W1, DAY_STR)]["usd_basis"] == "400"
        assert db.accruals[(W1, DAY_STR)]["credits"] == "0.400000"
        assert db.credit_calls[0]["credits"] == pytest.approx(0.4)

    def test_selects_the_highest_covering_tier(self, db):
        db.observe(W1, 2000)  # tier 2 -> 2000/1000 * 2 = 4
        db.link(W1, 7)
        rewards.run_holdings_rewards_once(DAY)
        assert db.accruals[(W1, DAY_STR)]["credits"] == "4.000000"

    def test_wallet_with_no_snapshot_basis_is_skipped(self, db):
        db.batches[(W1, DAY_STR)] = None
        db.link(W1, 7)
        result = rewards.run_holdings_rewards_once(DAY)
        assert result["skipped"]["no_snapshots"] == 1
        assert db.accruals == {}

    def test_no_covering_tier_skips_without_an_accrual(self, db):
        db.rates = [{"id": 2, "min_usd": "1000", "credits_per_1k_usd_per_day": "2.000000"}]
        db.observe(W1, 10)
        db.link(W1, 7)
        result = rewards.run_holdings_rewards_once(DAY)
        assert result["skipped"]["no_rate_tier"] == 1
        assert db.accruals == {}
        assert db.credit_calls == []

    def test_value_too_small_to_earn_a_credit_unit_is_skipped(self, db):
        db.observe(W1, "0.0001")  # 0.0001/1000 * 1 rounds down to 0 at 6dp
        db.link(W1, 7)
        result = rewards.run_holdings_rewards_once(DAY)
        assert result["skipped"]["zero_credits"] == 1
        assert db.accruals == {}
        assert db.credit_calls == []

    def test_credits_round_down_never_up(self, db):
        db.observe(W1, "1.9999999")
        db.link(W1, 7)
        rewards.run_holdings_rewards_once(DAY)
        assert db.accruals[(W1, DAY_STR)]["credits"] == "0.001999"


class TestPerAccountCap:
    def test_credits_are_capped_per_day(self, db, monkeypatch):
        monkeypatch.setattr(rewards.Config, "HOLDINGS_DAILY_CAP_CREDITS", 5.0, raising=False)
        db.observe(W1, 100000)  # would earn 200
        db.link(W1, 7)
        result = rewards.run_holdings_rewards_once(DAY)
        assert db.accruals[(W1, DAY_STR)]["credits"] == "5.000000"
        assert result["capped"] == 1

    def test_cap_is_per_account_not_per_wallet(self, db, monkeypatch):
        """Two wallets on one account share one daily cap -- otherwise
        splitting a balance across wallets would multiply the ceiling."""
        monkeypatch.setattr(rewards.Config, "HOLDINGS_DAILY_CAP_CREDITS", 5.0, raising=False)
        db.observe(W1, 100000)
        db.observe(W2, 100000)
        db.link(W1, 7)
        db.link(W2, 7)
        rewards.run_holdings_rewards_once(DAY)
        total = sum(Decimal(r["credits"]) for r in db.accruals.values())
        assert total == Decimal("5.000000")

    def test_separate_accounts_each_get_their_own_cap(self, db, monkeypatch):
        monkeypatch.setattr(rewards.Config, "HOLDINGS_DAILY_CAP_CREDITS", 5.0, raising=False)
        db.observe(W1, 100000)
        db.observe(W2, 100000)
        db.link(W1, 7)
        db.link(W2, 8)
        rewards.run_holdings_rewards_once(DAY)
        total = sum(Decimal(r["credits"]) for r in db.accruals.values())
        assert total == Decimal("10.000000")

    def test_account_already_at_the_cap_gets_nothing_more(self, db, monkeypatch):
        monkeypatch.setattr(rewards.Config, "HOLDINGS_DAILY_CAP_CREDITS", 5.0, raising=False)
        db.link(W1, 7)
        db.link(W2, 7)
        db.observe(W2, 100000)
        db.accruals[(W1, DAY_STR)] = {
            "id": 99,
            "wallet_address": W1,
            "reward_date": DAY_STR,
            "usd_basis": "100000",
            "credits": "5.000000",
            "status": "paid",
            "ledger_request_id": "x",
        }
        rewards.run_holdings_rewards_once(DAY)
        assert (W2, DAY_STR) not in db.accruals


class TestGlobalBudget:
    def test_run_never_exceeds_the_global_budget(self, db, monkeypatch):
        monkeypatch.setattr(
            rewards.Config, "HOLDINGS_GLOBAL_DAILY_BUDGET_CREDITS", 7.0, raising=False
        )
        for i, wallet in enumerate((W1, W2, W3)):
            db.observe(wallet, 5000)  # 5000/1000 * 2 = 10 each
            db.link(wallet, 10 + i)
        result = rewards.run_holdings_rewards_once(DAY)
        total = sum(Decimal(r["credits"]) for r in db.accruals.values())
        assert total <= Decimal("7")
        assert result["budget_skipped"] >= 1

    def test_wallets_skipped_for_budget_are_reported(self, db, monkeypatch):
        monkeypatch.setattr(
            rewards.Config, "HOLDINGS_GLOBAL_DAILY_BUDGET_CREDITS", 1.0, raising=False
        )
        for i, wallet in enumerate((W1, W2, W3)):
            db.observe(wallet, 5000)
            db.link(wallet, 10 + i)
        result = rewards.run_holdings_rewards_once(DAY)
        assert result["budget_skipped"] == 3
        assert result["skipped"]["budget_exhausted"] == 3
        assert result["budget_exhausted"] is True
        assert db.accruals == {}

    def test_budget_order_is_reproducible_for_one_date(self, db, monkeypatch):
        monkeypatch.setattr(
            rewards.Config, "HOLDINGS_GLOBAL_DAILY_BUDGET_CREDITS", 10.0, raising=False
        )
        for i, wallet in enumerate((W3, W2, W1)):
            db.observe(wallet, 5000)
            db.link(wallet, 10 + i)
        rewards.run_holdings_rewards_once(DAY)
        winner = list(db.accruals)
        assert len(winner) == 1
        assert rewards.budget_order([W1, W2, W3], DAY_STR)[0] == winner[0][0]

    def test_a_rerun_does_not_hand_out_the_budget_twice(self, db, monkeypatch):
        """Accruals already recorded for the date consume budget, so a
        second run for the same date cannot grant a second budget's worth."""
        monkeypatch.setattr(
            rewards.Config, "HOLDINGS_GLOBAL_DAILY_BUDGET_CREDITS", 10.0, raising=False
        )
        for i, wallet in enumerate((W1, W2, W3)):
            db.observe(wallet, 5000)
            db.link(wallet, 10 + i)
        rewards.run_holdings_rewards_once(DAY)
        first = sum(Decimal(r["credits"]) for r in db.accruals.values())
        rewards.run_holdings_rewards_once(DAY)
        second = sum(Decimal(r["credits"]) for r in db.accruals.values())
        assert first == second == Decimal("10")


class TestPayoutAndIdempotency:
    def test_accrual_is_written_pending_before_the_credit_is_granted(self, db, monkeypatch):
        order: list[str] = []
        real_create = db.create_holdings_accrual
        real_credit = db.add_credits_to_user

        def spy_create(*args, **kwargs):
            row = real_create(*args, **kwargs)
            order.append(f"create:{row['status']}")
            return row

        def spy_credit(**kwargs):
            order.append("credit")
            return real_credit(**kwargs)

        monkeypatch.setattr(rewards, "create_holdings_accrual", spy_create)
        monkeypatch.setattr(rewards, "add_credits_to_user", spy_credit)

        db.observe(W1, 1000)
        db.link(W1, 7)
        rewards.run_holdings_rewards_once(DAY)
        assert order == ["create:pending", "credit"]

    def test_ledger_request_id_is_the_idempotency_key(self, db):
        db.observe(W1, 1000)
        db.link(W1, 7)
        rewards.run_holdings_rewards_once(DAY)
        call = db.credit_calls[0]
        assert call["request_id"] == f"holdings_reward:{W1}:{DAY_STR}"
        assert call["transaction_type"] == "holdings_reward"
        assert db.accruals[(W1, DAY_STR)]["ledger_request_id"] == call["request_id"]

    def test_second_run_pays_nothing_more(self, db):
        db.observe(W1, 1000)
        db.link(W1, 7)
        rewards.run_holdings_rewards_once(DAY)
        second = rewards.run_holdings_rewards_once(DAY)
        assert len(db.credit_calls) == 1
        assert second["paid"] == 0
        assert second["already"] == 1

    def test_failed_credit_write_leaves_the_accrual_pending(self, db):
        db.observe(W1, 1000)
        db.link(W1, 7)
        db.fail_next_credit_calls = 1
        result = rewards.run_holdings_rewards_once(DAY)
        assert result["pending"] == 1
        assert db.accruals[(W1, DAY_STR)]["status"] == "pending"
        assert db.credit_calls == []

    def test_a_later_run_retries_a_pending_accrual(self, db):
        db.observe(W1, 1000)
        db.link(W1, 7)
        db.fail_next_credit_calls = 1
        rewards.run_holdings_rewards_once(DAY)
        second = rewards.run_holdings_rewards_once(DAY)
        assert second["paid"] == 1
        assert db.accruals[(W1, DAY_STR)]["status"] == "paid"

    def test_one_failing_wallet_does_not_abort_the_run(self, db, monkeypatch):
        db.observe(W1, 1000)
        db.observe(W2, 1000)
        db.link(W1, 7)
        db.link(W2, 8)

        real_basis = db.get_sweep_totals_for_date

        def exploding_basis(wallet_address, day):
            if wallet_address.lower() == W1:
                raise RuntimeError("boom")
            return real_basis(wallet_address, day)

        monkeypatch.setattr(rewards, "get_sweep_totals_for_date", exploding_basis)
        result = rewards.run_holdings_rewards_once(DAY)
        assert result["errors"] == 1
        assert result["paid"] == 1


class TestUnlinkedWallets:
    def test_unlinked_wallet_accrues_pending_and_is_not_paid(self, db):
        db.observe(W1, 1000)
        result = rewards.run_holdings_rewards_once(DAY)
        assert result["pending"] == 1
        assert db.accruals[(W1, DAY_STR)]["status"] == "pending"
        assert db.credit_calls == []

    def test_pending_accrual_is_paid_when_the_wallet_links(self, db):
        db.observe(W1, 1000)
        rewards.run_holdings_rewards_once(DAY)
        db.link(W1, 7)
        rewards.pay_pending_holdings_for_wallet(W1, 7)
        assert db.accruals[(W1, DAY_STR)]["status"] == "paid"
        assert db.credit_calls[0]["user_id"] == 7

    def test_pay_on_link_is_a_no_op_while_the_feature_is_off(self, db, monkeypatch):
        db.observe(W1, 1000)
        rewards.run_holdings_rewards_once(DAY)
        monkeypatch.setattr(rewards.Config, "HOLDINGS_REWARDS_ENABLED", False, raising=False)
        rewards.pay_pending_holdings_for_wallet(W1, 7)
        assert db.credit_calls == []

    def test_pay_on_link_never_raises(self, db, monkeypatch):
        def boom(_address):
            raise RuntimeError("boom")

        monkeypatch.setattr(rewards, "list_pending_holdings_accruals", boom)
        rewards.pay_pending_holdings_for_wallet(W1, 7)  # must not raise

    def test_a_later_run_sweeps_pending_accruals_from_earlier_days(self, db):
        earlier = date(2026, 9, 13)
        db.observe(W1, 1000, day=earlier)
        rewards.run_holdings_rewards_once(earlier)  # unlinked -> pending
        db.link(W1, 7)
        db.observe(W2, 1000)
        db.link(W2, 7)
        result = rewards.run_holdings_rewards_once(DAY)
        assert db.accruals[(W1, earlier.isoformat())]["status"] == "paid"
        assert result["retried_paid"] == 1


class TestViews:
    def test_rate_table_view_is_api_shaped_strings(self, db):
        assert rewards.rate_table_view() == [
            {"min_usd": "0", "credits_per_1k_usd_per_day": "1.000000"},
            {"min_usd": "1000", "credits_per_1k_usd_per_day": "2.000000"},
        ]

    def test_estimate_is_capped_at_the_daily_ceiling(self, db, monkeypatch):
        monkeypatch.setattr(rewards.Config, "HOLDINGS_DAILY_CAP_CREDITS", 5.0, raising=False)
        estimate = rewards.estimate_daily_credits(Decimal("100000"))
        assert estimate["estimated_credits_per_day"] == "5.000000"
        assert estimate["uncapped_credits_per_day"] == "200.000000"
        assert estimate["rate_credits_per_1k_usd"] == "2.000000"

    def test_estimate_with_no_covering_tier_is_zero(self, db):
        db.rates = [{"id": 2, "min_usd": "1000", "credits_per_1k_usd_per_day": "2.000000"}]
        estimate = rewards.estimate_daily_credits(Decimal("10"))
        assert estimate["estimated_credits_per_day"] == "0"


class TestMinimumBatches:
    """ "Lowest of the day" is only an anti-farm rule when the day has more
    than one reading. A wallet can legitimately end a day with a single
    recorded sweep, because the observation sweep drops a whole batch on an
    incomplete chain read or a missing price -- and then the minimum is just
    that one farmable moment."""

    def test_a_single_observed_sweep_is_not_paid(self, db):
        db.observe(W1, 1000, batches=1)
        db.link(W1, 7)
        result = rewards.run_holdings_rewards_once(DAY)
        assert result["skipped"]["too_few_batches"] == 1
        assert db.accruals == {}
        assert db.credit_calls == []

    def test_two_observed_sweeps_are_paid(self, db):
        db.observe(W1, 1000, batches=2)
        db.link(W1, 7)
        result = rewards.run_holdings_rewards_once(DAY)
        assert result["paid"] == 1
        assert result["skipped"]["too_few_batches"] == 0

    def test_the_requirement_is_configurable(self, db, monkeypatch):
        monkeypatch.setattr(
            rewards.Config, "HOLDINGS_MIN_SNAPSHOT_BATCHES_PER_DAY", 3, raising=False
        )
        db.observe(W1, 1000, batches=2)
        db.observe(W2, 1000, batches=3)
        db.link(W1, 7)
        db.link(W2, 8)
        result = rewards.run_holdings_rewards_once(DAY)
        assert result["skipped"]["too_few_batches"] == 1
        assert list(db.accruals) == [(W2, DAY_STR)]

    def test_requirement_is_clamped_to_the_sweeps_actually_scheduled(self, db, monkeypatch):
        """Requiring more sweeps than the schedule takes would pay nobody --
        a misconfiguration must not be able to switch the feature off."""
        monkeypatch.setattr(rewards.Config, "HOLDINGS_SNAPSHOTS_PER_DAY", 2, raising=False)
        monkeypatch.setattr(
            rewards.Config, "HOLDINGS_MIN_SNAPSHOT_BATCHES_PER_DAY", 99, raising=False
        )
        db.observe(W1, 1000, batches=2)
        db.link(W1, 7)
        result = rewards.run_holdings_rewards_once(DAY)
        assert result["required_batches"] == 2
        assert result["paid"] == 1

    def test_requirement_of_one_never_drops_below_one(self, db, monkeypatch):
        monkeypatch.setattr(rewards.Config, "HOLDINGS_SNAPSHOTS_PER_DAY", 1, raising=False)
        monkeypatch.setattr(
            rewards.Config, "HOLDINGS_MIN_SNAPSHOT_BATCHES_PER_DAY", 0, raising=False
        )
        db.observe(W1, 1000, batches=1)
        db.link(W1, 7)
        result = rewards.run_holdings_rewards_once(DAY)
        assert result["required_batches"] == 1
        assert result["paid"] == 1

    def test_the_basis_is_still_the_lowest_of_the_observed_sweeps(self, db):
        db.batches[(W1, DAY_STR)] = {
            "2026-09-14T00:00:00+00:00": Decimal("9000"),
            "2026-09-14T06:00:00+00:00": Decimal("400"),
            "2026-09-14T12:00:00+00:00": Decimal("7000"),
        }
        db.link(W1, 7)
        rewards.run_holdings_rewards_once(DAY)
        assert db.accruals[(W1, DAY_STR)]["usd_basis"] == "400"


class TestBudgetOrderRotation:
    def test_order_is_stable_for_one_date(self):
        wallets = [W1, W2, W3]
        assert rewards.budget_order(wallets, DAY_STR) == rewards.budget_order(
            list(reversed(wallets)), DAY_STR
        )

    def test_order_changes_between_dates(self):
        """Otherwise the same low addresses win the budget every single day
        it runs out, which is a permanent advantage, not a tie-break."""
        wallets = [f"0x{i:040x}" for i in range(40)]
        orders = {tuple(rewards.budget_order(wallets, f"2026-09-{d:02d}")) for d in range(1, 15)}
        assert len(orders) > 1

    def test_no_wallet_is_permanently_first(self):
        wallets = [f"0x{i:040x}" for i in range(40)]
        firsts = {rewards.budget_order(wallets, f"2026-09-{d:02d}")[0] for d in range(1, 15)}
        assert len(firsts) > 1

    def test_every_wallet_is_kept_exactly_once(self):
        wallets = [W1, W2, W3]
        ordered = rewards.budget_order(wallets, DAY_STR)
        assert sorted(ordered) == sorted(wallets)

    def test_a_rerun_of_one_date_pays_the_same_wallets(self, db, monkeypatch):
        monkeypatch.setattr(
            rewards.Config, "HOLDINGS_GLOBAL_DAILY_BUDGET_CREDITS", 10.0, raising=False
        )
        for i, wallet in enumerate((W1, W2, W3)):
            db.observe(wallet, 5000)
            db.link(wallet, 10 + i)
        rewards.run_holdings_rewards_once(DAY)
        paid_first = set(db.accruals)
        rewards.run_holdings_rewards_once(DAY)
        assert set(db.accruals) == paid_first


class TestSkipReasonBreakdown:
    def test_each_reason_is_counted_separately(self, db, monkeypatch):
        monkeypatch.setattr(
            rewards.Config, "HOLDINGS_GLOBAL_DAILY_BUDGET_CREDITS", 1.0, raising=False
        )
        db.observe(W1, 1000, batches=1)  # too few sweeps
        db.batches[(W2, DAY_STR)] = None  # nothing observed
        db.observe(W3, 5000)  # earns 10, budget is 1
        for i, wallet in enumerate((W1, W2, W3)):
            db.link(wallet, 10 + i)

        result = rewards.run_holdings_rewards_once(DAY)

        assert result["skipped"] == {
            "no_snapshots": 1,
            "too_few_batches": 1,
            "no_rate_tier": 0,
            "zero_credits": 0,
            "budget_exhausted": 1,
        }
        assert result["skipped_total"] == 3

    def test_disabled_is_its_own_outcome_not_a_skip_count(self, db, monkeypatch):
        monkeypatch.setattr(rewards.Config, "HOLDINGS_REWARDS_ENABLED", False, raising=False)
        assert rewards.run_holdings_rewards_once(DAY) == {"skipped": "disabled"}
