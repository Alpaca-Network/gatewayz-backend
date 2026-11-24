"""Tests for src.services.staking_rewards -- the staker-credit pay path and
stake-sync staleness guard that the emission epoch job reuses."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

import src.services.staking_rewards as staking_rewards

ADDRESS = "0x" + "1" * 40
DAY = "2026-09-12"


class FakeStakingDB:
    """In-memory fake standing in for src.db.staking_rewards +
    src.db.wallet_stakes + src.db.user_wallets + src.db.users +
    src.db.credit_transactions, wired into the service module under test
    via monkeypatch."""

    def __init__(self):
        self.accruals: dict[int, dict] = {}
        self.linked: dict[str, dict] = {}
        self.max_synced_at: str | None = datetime.now(UTC).isoformat()
        self.credit_calls: list[dict] = []  # successful calls only
        self.all_credit_attempts: list[dict] = []  # every call, success or fail
        self.fail_next_credit_calls = 0
        self._txn_id_seq = 1000

    def add_accrual(self, user_id=None, credits="0.050000", wayz=5000):
        row = {
            "id": len(self.accruals) + 1,
            "wallet_address": ADDRESS,
            "user_id": user_id,
            "reward_date": DAY,
            "staked_amount_wei": str(int(wayz) * 10**18),
            "rate_id": None,
            "credits": credits,
            "status": "pending",
            "credit_transaction_id": None,
            "skip_reason": None,
            "paid_at": None,
        }
        self.accruals[row["id"]] = row
        return dict(row)

    # -- src.db.staking_rewards ------------------------------------------------
    def mark_accrual_paid(self, accrual_id, user_id, credit_transaction_id, paid_at):
        self.accruals[accrual_id].update(
            status="paid",
            user_id=user_id,
            credit_transaction_id=credit_transaction_id,
            paid_at=paid_at,
            skip_reason=None,
        )
        return True

    def mark_accrual_pending_failed(self, accrual_id, skip_reason):
        self.accruals[accrual_id]["skip_reason"] = skip_reason
        return True

    # -- src.db.wallet_stakes ---------------------------------------------------
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
    monkeypatch.setattr(staking_rewards, "mark_accrual_paid", fake.mark_accrual_paid)
    monkeypatch.setattr(
        staking_rewards, "mark_accrual_pending_failed", fake.mark_accrual_pending_failed
    )
    monkeypatch.setattr(staking_rewards, "get_max_last_synced_at", fake.get_max_last_synced_at)
    monkeypatch.setattr(staking_rewards, "get_wallet", fake.get_wallet)
    monkeypatch.setattr(staking_rewards, "add_credits_to_user", fake.add_credits_to_user)
    monkeypatch.setattr(
        staking_rewards, "get_transaction_by_request_id", fake.get_transaction_by_request_id
    )
    monkeypatch.setattr(staking_rewards.Config, "WAYZ_STAKING_SYNC_INTERVAL_MINUTES", 15)
    return fake


class TestStaleSyncGuard:
    def test_never_synced_is_stale(self, store):
        store.max_synced_at = None
        assert staking_rewards.is_stake_sync_stale() is True

    def test_unparseable_timestamp_is_stale(self, store):
        store.max_synced_at = "not-a-timestamp"
        assert staking_rewards.is_stake_sync_stale() is True

    def test_old_last_synced_at_is_stale(self, store):
        store.max_synced_at = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
        assert staking_rewards.is_stake_sync_stale() is True

    def test_fresh_sync_is_not_stale(self, store):
        store.max_synced_at = datetime.now(UTC).isoformat()
        assert staking_rewards.is_stake_sync_stale() is False


class TestPayPendingAccrual:
    def test_linked_accrual_is_paid_once_with_a_deterministic_request_id(self, store):
        accrual = store.add_accrual(user_id=7)

        outcome, credits = staking_rewards.pay_pending_accrual(accrual, capped=True)

        assert (outcome, credits) == ("paid", Decimal("0.050000"))
        assert len(store.credit_calls) == 1
        call = store.credit_calls[0]
        assert call["user_id"] == 7
        assert call["credits"] == 0.05
        assert call["transaction_type"] == "staking_reward"
        assert call["request_id"] == staking_rewards._staking_request_id(ADDRESS, DAY)
        assert call["metadata"]["capped"] is True
        row = store.accruals[accrual["id"]]
        assert row["status"] == "paid"
        assert row["user_id"] == 7
        assert row["credit_transaction_id"] is not None

    def test_unlinked_wallet_is_left_pending(self, store):
        accrual = store.add_accrual(user_id=None)

        assert staking_rewards.pay_pending_accrual(accrual) == ("pending", Decimal(0))
        assert store.all_credit_attempts == []
        assert store.accruals[accrual["id"]]["status"] == "pending"

    def test_inactive_wallet_owner_is_left_pending(self, store):
        store.linked[ADDRESS] = {"user_id": 7, "is_active": False}
        accrual = store.add_accrual(user_id=None)

        assert staking_rewards.pay_pending_accrual(accrual) == ("pending", Decimal(0))
        assert store.all_credit_attempts == []

    def test_wallet_linked_since_the_accrual_is_paid_to_its_owner(self, store):
        store.linked[ADDRESS] = {"user_id": 99, "is_active": True}
        accrual = store.add_accrual(user_id=None)

        outcome, _credits = staking_rewards.pay_pending_accrual(accrual)

        assert outcome == "paid"
        assert store.credit_calls[0]["user_id"] == 99
        assert store.accruals[accrual["id"]]["user_id"] == 99

    def test_failed_credit_write_leaves_pending_and_records_why(self, store):
        accrual = store.add_accrual(user_id=7)
        store.fail_next_credit_calls = 1

        assert staking_rewards.pay_pending_accrual(accrual) == ("pending", Decimal(0))

        row = store.accruals[accrual["id"]]
        assert row["status"] == "pending"
        assert row["skip_reason"] == "ValueError"
        assert len(store.all_credit_attempts) == 1
        assert store.credit_calls == []
