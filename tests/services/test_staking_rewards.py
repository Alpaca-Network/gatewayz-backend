"""Tests for src.services.staking_rewards (gatewayz-backend staking rewards
-- daily job paying WAYZ stakers in inference credits)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

import src.services.staking_rewards as staking_rewards
from src.services.staking_rewards import StakingRewardsStaleError

ADDRESS = "0x" + "1" * 40


class FakeStakingDB:
    """In-memory fake standing in for src.db.staking_rewards +
    src.db.wallet_stakes + src.db.user_wallets + src.db.users +
    src.db.credit_transactions, wired into the service module under test
    via monkeypatch. Lets each test drive a realistic multi-call scenario
    (idempotency across runs, retry across runs) without touching Supabase.
    """

    def __init__(self):
        self.accruals: dict[tuple[str, str], dict] = {}
        self._next_id = 1
        self.rates = [
            {"id": 1, "min_stake_wayz": "0", "credits_per_1k_wayz_per_day": "0.010000"},
            {"id": 2, "min_stake_wayz": "10000", "credits_per_1k_wayz_per_day": "0.012000"},
            {"id": 3, "min_stake_wayz": "100000", "credits_per_1k_wayz_per_day": "0.015000"},
        ]
        self.wallets_with_stake: list[dict] = []
        self.linked: dict[str, dict] = {}
        self.max_synced_at = datetime.now(UTC).isoformat()
        self.credit_calls: list[dict] = []  # successful calls only
        self.all_credit_attempts: list[dict] = []  # every call, success or fail
        self.fail_next_credit_calls = 0
        self._txn_id_seq = 1000

    # -- src.db.staking_rewards ------------------------------------------------
    def get_active_rates(self):
        return list(self.rates)

    def get_accrual(self, wallet_address, reward_date):
        return self.accruals.get((wallet_address.lower(), reward_date))

    def create_accrual(
        self,
        wallet_address,
        reward_date,
        staked_amount_wei,
        rate_id,
        credits,
        status,
        user_id=None,
        skip_reason=None,
    ):
        key = (wallet_address.lower(), reward_date)
        if key in self.accruals:
            return None
        row = {
            "id": self._next_id,
            "wallet_address": wallet_address.lower(),
            "user_id": user_id,
            "reward_date": reward_date,
            "staked_amount_wei": staked_amount_wei,
            "rate_id": rate_id,
            "credits": credits,
            "status": status,
            "credit_transaction_id": None,
            "skip_reason": skip_reason,
            "paid_at": None,
        }
        self._next_id += 1
        self.accruals[key] = row
        return dict(row)

    def mark_accrual_paid(self, accrual_id, user_id, credit_transaction_id, paid_at):
        for row in self.accruals.values():
            if row["id"] == accrual_id:
                row.update(
                    status="paid",
                    user_id=user_id,
                    credit_transaction_id=credit_transaction_id,
                    paid_at=paid_at,
                    skip_reason=None,
                )
                return True
        return False

    def mark_accrual_pending_failed(self, accrual_id, skip_reason):
        for row in self.accruals.values():
            if row["id"] == accrual_id:
                row["skip_reason"] = skip_reason
                return True
        return False

    def list_pending_accruals(self, min_reward_date, wallet_address=None):
        out = []
        for row in self.accruals.values():
            if row["status"] != "pending":
                continue
            if row["reward_date"] < min_reward_date:
                continue
            if wallet_address is not None and row["wallet_address"] != wallet_address.lower():
                continue
            out.append(dict(row))
        return out

    # -- src.db.wallet_stakes ---------------------------------------------------
    def list_wallets_with_stake(self):
        return list(self.wallets_with_stake)

    def get_max_last_synced_at(self):
        return self.max_synced_at

    # -- src.db.user_wallets ------------------------------------------------
    def get_wallet(self, address):
        return self.linked.get(address.lower())

    # -- src.db.users / src.db.credit_transactions ---------------------------
    def add_credits_to_user(self, **kwargs):
        self.all_credit_attempts.append(kwargs)
        if self.fail_next_credit_calls > 0:
            self.fail_next_credit_calls -= 1
            raise ValueError("simulated ledger failure")
        self.credit_calls.append(kwargs)

    def get_transaction_by_request_id(self, request_id):
        for call in self.credit_calls:
            if call.get("request_id") == request_id:
                self._txn_id_seq += 1
                return {"id": self._txn_id_seq}
        return None


@pytest.fixture
def store(monkeypatch):
    fake = FakeStakingDB()
    monkeypatch.setattr(staking_rewards, "get_active_rates", fake.get_active_rates)
    monkeypatch.setattr(staking_rewards, "get_accrual", fake.get_accrual)
    monkeypatch.setattr(staking_rewards, "create_accrual", fake.create_accrual)
    monkeypatch.setattr(staking_rewards, "mark_accrual_paid", fake.mark_accrual_paid)
    monkeypatch.setattr(
        staking_rewards, "mark_accrual_pending_failed", fake.mark_accrual_pending_failed
    )
    monkeypatch.setattr(staking_rewards, "list_pending_accruals", fake.list_pending_accruals)
    monkeypatch.setattr(staking_rewards, "list_wallets_with_stake", fake.list_wallets_with_stake)
    monkeypatch.setattr(staking_rewards, "get_max_last_synced_at", fake.get_max_last_synced_at)
    monkeypatch.setattr(staking_rewards, "get_wallet", fake.get_wallet)
    monkeypatch.setattr(staking_rewards, "add_credits_to_user", fake.add_credits_to_user)
    monkeypatch.setattr(
        staking_rewards, "get_transaction_by_request_id", fake.get_transaction_by_request_id
    )
    monkeypatch.setattr(staking_rewards.Config, "STAKING_REWARDS_ENABLED", True)
    monkeypatch.setattr(staking_rewards.Config, "STAKING_REWARDS_DAILY_CAP_CREDITS", 50.0)
    monkeypatch.setattr(staking_rewards.Config, "STAKING_REWARDS_MIN_CREDITS", 0.0001)
    monkeypatch.setattr(staking_rewards.Config, "WAYZ_STAKING_SYNC_INTERVAL_MINUTES", 15)
    return fake


DAY1 = date(2026, 9, 9)
DAY2 = date(2026, 9, 10)


def _stake_row(address, wayz_amount):
    wei = str(int(wayz_amount) * 10**18)
    return {"wallet_address": address, "staked_amount": wei, "last_synced_at": "t"}


class TestDisabledByDefault:
    def test_disabled_skips_without_touching_db(self, monkeypatch):
        monkeypatch.setattr(staking_rewards.Config, "STAKING_REWARDS_ENABLED", False)

        def _boom():
            raise AssertionError("must not be called when disabled")

        monkeypatch.setattr(staking_rewards, "list_wallets_with_stake", _boom)
        result = staking_rewards.run_staking_rewards_once(DAY1)
        assert result == {"skipped": "disabled"}


class TestStaleSyncGuard:
    def test_empty_wallet_stakes_raises_stale(self, store):
        store.wallets_with_stake = []
        with pytest.raises(StakingRewardsStaleError):
            staking_rewards.run_staking_rewards_once(DAY1)

    def test_old_last_synced_at_raises_stale(self, store):
        store.wallets_with_stake = [_stake_row(ADDRESS, 5000)]
        store.max_synced_at = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
        with pytest.raises(StakingRewardsStaleError):
            staking_rewards.run_staking_rewards_once(DAY1)

    def test_fresh_sync_does_not_raise(self, store):
        store.wallets_with_stake = [_stake_row(ADDRESS, 5000)]
        store.linked[ADDRESS] = {"user_id": 1, "is_active": True}
        store.max_synced_at = datetime.now(UTC).isoformat()
        result = staking_rewards.run_staking_rewards_once(DAY1)
        assert result["reward_date"] == DAY1.isoformat()


class TestIdempotency:
    def test_same_wallet_date_twice_pays_once(self, store):
        store.wallets_with_stake = [_stake_row(ADDRESS, 5000)]
        store.linked[ADDRESS] = {"user_id": 7, "is_active": True}

        first = staking_rewards.run_staking_rewards_once(DAY1)
        second = staking_rewards.run_staking_rewards_once(DAY1)

        assert first["paid"] == 1
        assert second["paid"] == 1  # already-paid row still counted, but...
        assert len(store.credit_calls) == 1  # ...only ONE actual ledger write
        assert second["credits_paid"] == "0"  # nothing NEW was paid this run
        assert store.accruals[(ADDRESS, DAY1.isoformat())]["status"] == "paid"


class TestBelowMin:
    def test_tiny_stake_is_skipped_not_paid(self, store):
        store.wallets_with_stake = [
            {"wallet_address": ADDRESS, "staked_amount": "1", "last_synced_at": "t"}
        ]
        store.linked[ADDRESS] = {"user_id": 7, "is_active": True}

        result = staking_rewards.run_staking_rewards_once(DAY1)

        assert result["skipped"] == 1
        assert result["paid"] == 0
        assert store.credit_calls == []
        row = store.accruals[(ADDRESS, DAY1.isoformat())]
        assert row["status"] == "skipped"
        assert row["skip_reason"] == "below_min"


class TestDailyCap:
    def test_credits_are_capped_and_flagged(self, store):
        # 10,000,000 WAYZ at the top 0.015/1k tier => 150 credits/day, capped to 50.
        store.wallets_with_stake = [_stake_row(ADDRESS, 10_000_000)]
        store.linked[ADDRESS] = {"user_id": 7, "is_active": True}

        result = staking_rewards.run_staking_rewards_once(DAY1)

        assert result["paid"] == 1
        assert result["capped"] == 1
        assert result["credits_paid"] == "50.000000"
        assert store.credit_calls[0]["metadata"]["capped"] is True


class TestUnlinkedWallet:
    def test_unlinked_wallet_is_pending_not_paid(self, store):
        store.wallets_with_stake = [_stake_row(ADDRESS, 5000)]
        # no entry in store.linked -> get_wallet(address) returns None

        result = staking_rewards.run_staking_rewards_once(DAY1)

        assert result["pending"] == 1
        assert result["paid"] == 0
        assert store.credit_calls == []
        row = store.accruals[(ADDRESS, DAY1.isoformat())]
        assert row["status"] == "pending"
        assert row["user_id"] is None

    def test_inactive_wallet_owner_is_pending_not_paid(self, store):
        store.wallets_with_stake = [_stake_row(ADDRESS, 5000)]
        store.linked[ADDRESS] = {"user_id": 7, "is_active": False}

        result = staking_rewards.run_staking_rewards_once(DAY1)

        assert result["pending"] == 1
        assert store.credit_calls == []


class TestPaidOnLink:
    def test_pay_pending_for_wallet_pays_and_stamps_user_id(self, store):
        store.wallets_with_stake = [_stake_row(ADDRESS, 5000)]
        staking_rewards.run_staking_rewards_once(DAY1)  # creates a pending, unlinked accrual
        assert store.accruals[(ADDRESS, DAY1.isoformat())]["status"] == "pending"

        staking_rewards.pay_pending_for_wallet(ADDRESS, user_id=99)

        row = store.accruals[(ADDRESS, DAY1.isoformat())]
        assert row["status"] == "paid"
        assert row["user_id"] == 99
        assert len(store.credit_calls) == 1
        assert store.credit_calls[0]["user_id"] == 99

    def test_pay_pending_for_wallet_noops_when_disabled(self, store, monkeypatch):
        monkeypatch.setattr(staking_rewards.Config, "STAKING_REWARDS_ENABLED", False)

        def _boom(*_a, **_kw):
            raise AssertionError("must not be called when disabled")

        monkeypatch.setattr(staking_rewards, "list_pending_accruals", _boom)
        staking_rewards.pay_pending_for_wallet(ADDRESS, user_id=99)  # must not raise


class TestLedgerFailureRetried:
    def test_failed_credit_write_leaves_pending_and_is_retried_next_run(self, store):
        store.wallets_with_stake = [_stake_row(ADDRESS, 5000)]
        store.linked[ADDRESS] = {"user_id": 7, "is_active": True}
        store.fail_next_credit_calls = 1

        first = staking_rewards.run_staking_rewards_once(DAY1)
        assert first["pending"] == 1
        assert first["paid"] == 0
        row = store.accruals[(ADDRESS, DAY1.isoformat())]
        assert row["status"] == "pending"
        assert row["skip_reason"] == "ValueError"
        assert len(store.all_credit_attempts) == 1
        assert store.credit_calls == []

        second = staking_rewards.run_staking_rewards_once(DAY2)

        assert store.accruals[(ADDRESS, DAY1.isoformat())]["status"] == "paid"
        assert len(store.credit_calls) == 2  # DAY2's own reward + DAY1's retried reward


class TestRateTierSelection:
    def test_boundaries_use_decimal_not_float(self):
        rates = [
            {"id": 1, "min_stake_wayz": "0", "credits_per_1k_wayz_per_day": "0.01"},
            {"id": 2, "min_stake_wayz": "10000", "credits_per_1k_wayz_per_day": "0.012"},
            {"id": 3, "min_stake_wayz": "100000", "credits_per_1k_wayz_per_day": "0.015"},
        ]
        assert staking_rewards._select_rate(Decimal("9999.999999"), rates)["id"] == 1
        assert staking_rewards._select_rate(Decimal("10000"), rates)["id"] == 2
        assert staking_rewards._select_rate(Decimal("99999.999999"), rates)["id"] == 2
        assert staking_rewards._select_rate(Decimal("100000"), rates)["id"] == 3
        assert staking_rewards._select_rate(Decimal("100000.000001"), rates)["id"] == 3

    def test_no_covering_tier_returns_none(self):
        rates = [{"id": 2, "min_stake_wayz": "10000", "credits_per_1k_wayz_per_day": "0.012"}]
        assert staking_rewards._select_rate(Decimal("1"), rates) is None
