"""Tests for src.services.delegation.rewards -- the daily accrual.

Invariants: basis is the day's LOWEST measurement; a day with too few
measurements is skipped; per-account cap across wallets AND assets; global
budget; pending written before credits move and idempotent on re-run; a
paused (or unknown-pause) asset accrues and pays nothing.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

import src.services.delegation.rewards as rewards

DAY = date(2026, 10, 7)
DAY_STR = "2026-10-07"
W1 = "0x" + "1" * 40
W2 = "0x" + "2" * 40
ADA1 = "stake1uyehkck0lajq8gr28t9uxnuvgcqrc6070x3k9r8048z8y5gh6ffgw"


class FakeDB:
    def __init__(self):
        self.rates = {
            "eth": {"asset": "eth", "credits_per_1k_usd_per_day": "1.000000"},
            "ada": {"asset": "ada", "credits_per_1k_usd_per_day": "2.000000"},
        }
        self.measurements: dict[tuple[str, str], list[Decimal]] = {}
        self.accruals: dict[tuple[str, str, str], dict] = {}
        self.controls: dict | None = {"eth": {}, "ada": {}}
        self.linked: dict[str, dict] = {}
        self.credit_calls: list[dict] = []
        self.events: list[str] = []
        self.fail_credit = False
        self._id = 1

    def list_measured_pairs_for_date(self, day):
        return sorted(self.measurements)

    def get_measurements_for_date(self, wallet, asset, day):
        return list(self.measurements.get((wallet, asset), []))

    def list_accruals_for_date(self, day):
        return [dict(r) for r in self.accruals.values() if r["reward_date"] == str(day)]

    def list_accruals_for_wallet_date(self, wallet, day):
        return [
            dict(r)
            for r in self.accruals.values()
            if r["wallet_address"] == wallet and r["reward_date"] == str(day)
        ]

    def get_accrual(self, wallet, asset, day):
        row = self.accruals.get((wallet, asset, str(day)))
        return dict(row) if row else None

    def create_accrual(self, wallet, asset, day, basis, credits, rate, user_id):
        key = (wallet, asset, str(day))
        if key in self.accruals:
            return None
        row = {
            "id": self._id,
            "wallet_address": wallet,
            "asset": asset,
            "reward_date": str(day),
            "usd_basis": str(basis),
            "credits": str(credits),
            "status": "pending",
            "user_id": user_id,
        }
        self._id += 1
        self.accruals[key] = row
        self.events.append(f"create:{asset}:{wallet}")
        return dict(row)

    def mark_accrual_paid(self, accrual_id, request_id):
        for row in self.accruals.values():
            if row["id"] == accrual_id:
                row["status"] = "paid"
                row["ledger_request_id"] = request_id
                return dict(row)
        return None

    def list_pending_accruals_since(self, floor):
        return [dict(r) for r in self.accruals.values() if r["status"] == "pending"]

    def list_pending_accruals(self, wallet):
        return [
            dict(r)
            for r in self.accruals.values()
            if r["wallet_address"] == wallet and r["status"] == "pending"
        ]

    def get_active_rates(self):
        return dict(self.rates)

    def void_accrual(self, accrual_id):
        for row in self.accruals.values():
            if row["id"] == accrual_id:
                row["status"] = "void"
        return True

    def get_controls(self):
        return self.controls

    def get_wallet(self, address):
        return self.linked.get(address)

    def get_wallets_for_user(self, user_id):
        return [r for r in self.linked.values() if r["user_id"] == user_id]

    def add_credits_to_user(self, **kwargs):
        if self.fail_credit:
            raise RuntimeError("ledger down")
        self.events.append(f"credit:{kwargs['request_id']}")
        self.credit_calls.append(kwargs)


@pytest.fixture
def db(monkeypatch):
    fake = FakeDB()
    for name in (
        "list_measured_pairs_for_date",
        "get_measurements_for_date",
        "list_accruals_for_date",
        "list_accruals_for_wallet_date",
        "get_accrual",
        "create_accrual",
        "mark_accrual_paid",
        "list_pending_accruals_since",
        "list_pending_accruals",
        "get_active_rates",
        "get_controls",
        "get_wallet",
        "get_wallets_for_user",
        "add_credits_to_user",
        "void_accrual",
    ):
        monkeypatch.setattr(rewards, name, getattr(fake, name))
    monkeypatch.setattr(rewards.Config, "DELEGATED_STAKING_ENABLED", True)
    monkeypatch.setattr(rewards.Config, "DELEGATION_DAILY_CAP_CREDITS", Decimal("5"))
    monkeypatch.setattr(rewards.Config, "DELEGATION_GLOBAL_DAILY_BUDGET_CREDITS", Decimal("50"))
    monkeypatch.setattr(rewards.Config, "DELEGATION_MEASUREMENTS_PER_DAY", 4)
    monkeypatch.setattr(rewards.Config, "DELEGATION_MIN_MEASUREMENTS_PER_DAY", 2)
    monkeypatch.setattr(rewards, "eth_configured", lambda: True)
    monkeypatch.setattr(rewards, "ada_configured", lambda: True)
    return fake


def _link(db, address, user_id):
    db.linked[address] = {"wallet_address": address, "user_id": user_id}


def test_disabled_is_a_no_op(db, monkeypatch):
    monkeypatch.setattr(rewards.Config, "DELEGATED_STAKING_ENABLED", False)
    db.measurements[(W1, "eth")] = [Decimal(1000)] * 4
    assert rewards.run_delegation_accruals_once(DAY) == {"skipped": "disabled"}
    assert db.accruals == {}


def test_no_measurements_raises(db):
    with pytest.raises(rewards.DelegationMeasurementsMissingError):
        rewards.run_delegation_accruals_once(DAY)


def test_pays_on_the_lowest_measurement_of_the_day(db):
    _link(db, W1, 7)
    db.measurements[(W1, "eth")] = [Decimal(3000), Decimal(1000), Decimal(2000), Decimal(4000)]
    summary = rewards.run_delegation_accruals_once(DAY)
    assert summary["paid"] == 1
    row = db.accruals[(W1, "eth", DAY_STR)]
    assert Decimal(row["usd_basis"]) == Decimal(1000)
    assert Decimal(row["credits"]) == Decimal("1.000000")  # $1000 x 1 credit / $1k


def test_a_measured_zero_drags_the_day_to_zero(db):
    _link(db, W1, 7)
    db.measurements[(W1, "eth")] = [Decimal(5000), Decimal(0), Decimal(5000)]
    summary = rewards.run_delegation_accruals_once(DAY)
    assert summary["skipped"]["zero_credits"] == 1
    assert db.credit_calls == []


def test_too_few_measurements_pays_nothing(db):
    _link(db, W1, 7)
    db.measurements[(W1, "eth")] = [Decimal(5000)]
    summary = rewards.run_delegation_accruals_once(DAY)
    assert summary["skipped"]["too_few_measurements"] == 1
    assert db.accruals == {}


def test_pending_is_written_before_credits_and_request_id_is_namespaced(db):
    _link(db, W1, 7)
    db.measurements[(W1, "eth")] = [Decimal(1000)] * 2
    rewards.run_delegation_accruals_once(DAY)
    request_id = rewards.request_id_for("eth", W1, DAY_STR)
    assert db.events == [f"create:eth:{W1}", f"credit:{request_id}"]
    assert db.credit_calls[0]["transaction_type"] == "delegation_reward"
    assert db.credit_calls[0]["metadata"]["grant_key"] == f"delegation:eth:{W1}:{DAY_STR}"
    assert db.accruals[(W1, "eth", DAY_STR)]["ledger_request_id"] == request_id


def test_ledger_request_id_is_a_deterministic_uuid():
    import uuid

    first = rewards.request_id_for("eth", W1.upper().replace("0X", "0x"), DAY)
    assert uuid.UUID(first).version == 5
    assert first == rewards.request_id_for("eth", W1, DAY_STR)
    assert first != rewards.request_id_for("ada", W1, DAY_STR)


def test_relinking_several_pending_wallets_cannot_exceed_the_cap(db):
    # Measured, then unlinked before the accrual: each is capped on its own.
    db.measurements[(W1, "eth")] = [Decimal(5000)] * 2
    db.measurements[(W2, "eth")] = [Decimal(5000)] * 2
    rewards.run_delegation_accruals_once(DAY)
    assert {r["status"] for r in db.accruals.values()} == {"pending"}
    for wallet in (W1, W2):
        _link(db, wallet, 9)
        rewards.pay_pending_delegation_for_wallet(wallet, 9)
    paid = [r for r in db.accruals.values() if r["status"] == "paid"]
    assert sum(Decimal(r["credits"]) for r in paid) == Decimal(5)
    assert [r["status"] for r in db.accruals.values()].count("void") == 1


def test_rerun_is_idempotent(db):
    _link(db, W1, 7)
    db.measurements[(W1, "eth")] = [Decimal(1000)] * 2
    rewards.run_delegation_accruals_once(DAY)
    second = rewards.run_delegation_accruals_once(DAY)
    assert second["already"] == 1
    assert len(db.credit_calls) == 1


def test_account_cap_spans_wallets_and_assets(db):
    _link(db, W1, 7)
    _link(db, ADA1, 7)
    db.measurements[(W1, "eth")] = [Decimal(4000)] * 2  # 4 credits
    db.measurements[(ADA1, "ada")] = [Decimal(4000)] * 2  # 8 credits uncapped
    rewards.run_delegation_accruals_once(DAY)
    total = sum(Decimal(r["credits"]) for r in db.accruals.values())
    assert total == Decimal("5.000000")


def test_global_budget_stops_granting(db, monkeypatch):
    monkeypatch.setattr(rewards.Config, "DELEGATION_GLOBAL_DAILY_BUDGET_CREDITS", Decimal("6"))
    _link(db, W1, 1)
    _link(db, W2, 2)
    db.measurements[(W1, "eth")] = [Decimal(5000)] * 2
    db.measurements[(W2, "eth")] = [Decimal(5000)] * 2
    summary = rewards.run_delegation_accruals_once(DAY)
    assert summary["skipped"]["budget_exhausted"] == 1
    assert sum(Decimal(r["credits"]) for r in db.accruals.values()) == Decimal(5)


def test_existing_accruals_consume_budget_on_rerun(db, monkeypatch):
    monkeypatch.setattr(rewards.Config, "DELEGATION_GLOBAL_DAILY_BUDGET_CREDITS", Decimal("5"))
    _link(db, W1, 1)
    db.measurements[(W1, "eth")] = [Decimal(5000)] * 2
    rewards.run_delegation_accruals_once(DAY)
    _link(db, W2, 2)
    db.measurements[(W2, "eth")] = [Decimal(5000)] * 2
    summary = rewards.run_delegation_accruals_once(DAY)
    assert summary["skipped"]["budget_exhausted"] == 1


def test_paused_asset_accrues_nothing(db):
    _link(db, W1, 7)
    _link(db, ADA1, 8)
    db.controls = {"eth": {"accruals_paused": True}, "ada": {}}
    db.measurements[(W1, "eth")] = [Decimal(1000)] * 2
    db.measurements[(ADA1, "ada")] = [Decimal(1000)] * 2
    summary = rewards.run_delegation_accruals_once(DAY)
    assert summary["skipped"]["paused"] == 1
    assert list(db.accruals) == [(ADA1, "ada", DAY_STR)]


def test_unreadable_controls_pause_every_asset(db):
    _link(db, W1, 7)
    db.controls = None
    db.measurements[(W1, "eth")] = [Decimal(1000)] * 2
    summary = rewards.run_delegation_accruals_once(DAY)
    assert summary["paused_assets"] == ["ada", "eth"]
    assert db.accruals == {}


def test_unconfigured_asset_and_zero_rate_pay_nothing(db, monkeypatch):
    monkeypatch.setattr(rewards, "ada_configured", lambda: False)
    db.rates["eth"]["credits_per_1k_usd_per_day"] = "0"
    _link(db, W1, 7)
    _link(db, ADA1, 8)
    db.measurements[(W1, "eth")] = [Decimal(1000)] * 2
    db.measurements[(ADA1, "ada")] = [Decimal(1000)] * 2
    summary = rewards.run_delegation_accruals_once(DAY)
    assert summary["skipped"]["no_rate"] == 1
    assert summary["skipped"]["unconfigured"] == 1
    assert db.accruals == {}


def test_failed_credit_write_stays_pending_and_is_retried(db):
    _link(db, W1, 7)
    db.measurements[(W1, "eth")] = [Decimal(1000)] * 2
    db.fail_credit = True
    summary = rewards.run_delegation_accruals_once(DAY)
    assert summary["pending"] == 1
    assert db.accruals[(W1, "eth", DAY_STR)]["status"] == "pending"
    db.fail_credit = False
    summary = rewards.run_delegation_accruals_once(DAY)
    assert summary["paid"] == 1
    assert db.accruals[(W1, "eth", DAY_STR)]["status"] == "paid"


def test_unlinked_wallet_accrues_pending_and_is_paid_on_link(db):
    db.measurements[(W1, "eth")] = [Decimal(1000)] * 2
    summary = rewards.run_delegation_accruals_once(DAY)
    assert summary["pending"] == 1
    assert db.credit_calls == []
    rewards.pay_pending_delegation_for_wallet(W1, 9)
    assert db.credit_calls[0]["user_id"] == 9
    assert db.accruals[(W1, "eth", DAY_STR)]["status"] == "paid"


def test_pay_on_link_skips_paused_assets(db):
    db.measurements[(W1, "eth")] = [Decimal(1000)] * 2
    rewards.run_delegation_accruals_once(DAY)
    db.controls = {"eth": {"accruals_paused": True}}
    rewards.pay_pending_delegation_for_wallet(W1, 9)
    assert db.credit_calls == []


def test_suggested_rate_is_r_over_one_minus_margin(monkeypatch):
    monkeypatch.setattr(rewards.Config, "DELEGATION_INFERENCE_MARGIN", Decimal("0.20"))
    # R = $0.00008 per $ per day -> 1000 x 0.00008 / 0.8 = 0.1 credits per $1k.
    assert rewards.suggested_rate(Decimal("0.00008")) == Decimal("0.100000")
    assert rewards.suggested_rate(Decimal(0)) == Decimal(0)


def test_estimate_is_capped(db):
    estimate = rewards.estimate_daily_credits(
        [("eth", Decimal(4000)), ("ada", Decimal(4000))], db.rates
    )
    assert estimate == Decimal("5.000000")
