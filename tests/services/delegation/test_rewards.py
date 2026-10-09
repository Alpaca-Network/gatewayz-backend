"""Tests for src.services.delegation.rewards -- the daily accrual.

Invariants: basis is the day's LOWEST measurement; a day with too few
measurements is skipped; per-account cap across wallets AND assets; global
budget; pending written before credits move and idempotent on re-run; a
paused (or unknown-pause) asset accrues and pays nothing.
"""

from __future__ import annotations

import threading
import time
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
    """In-memory stand-in for src.db.delegation + user_wallets + users.

    reserve_accrual / claim_accrual mirror the SQL functions in
    20261008230000_delegated_staking.sql (section 6) line for line, including
    the per-date lock -- a threading.Lock here -- so the module under test can
    only stay inside the cap if it leaves every cap/budget decision to them.
    `race_delay` sleeps inside the plain reads, widening any read-then-write
    window a caller might still have.
    """

    def __init__(self):
        self.rates = {
            "eth": {"asset": "eth", "credits_per_1k_usd_per_day": "1.000000"},
            "ada": {"asset": "ada", "credits_per_1k_usd_per_day": "2.000000"},
        }
        self.measurements: dict[tuple[str, str], list[Decimal]] = {}
        self.accruals: dict[tuple[str, str, str], dict] = {}
        self.controls: dict | None = {
            "eth": {"accruals_paused": False},
            "ada": {"accruals_paused": False},
        }
        self.linked: dict[str, dict] = {}
        self.inactive_users: set[int] = set()
        self.credit_calls: list[dict] = []
        self.ledger: set[str] = set()
        self.events: list[str] = []
        self.fail_credit = False
        self.race_delay = 0.0
        self._id = 1
        self._date_lock = threading.Lock()
        self._mutex = threading.RLock()

    def _sleep(self):
        if self.race_delay:
            time.sleep(self.race_delay)

    # -- plain reads ----------------------------------------------------------
    def list_measured_pairs_for_date(self, day):
        return sorted(self.measurements)

    def get_measurements_for_date(self, wallet, asset, day):
        return list(self.measurements.get((wallet, asset), []))

    def get_accrual(self, wallet, asset, day):
        with self._mutex:
            row = self.accruals.get((wallet, asset, str(day)))
            row = dict(row) if row else None
        self._sleep()
        return row

    def list_pending_accruals_since(self, floor):
        with self._mutex:
            return [
                dict(r) for r in self.accruals.values() if r["status"] in ("pending", "claimed")
            ]

    def list_pending_accruals(self, wallet):
        with self._mutex:
            rows = [
                dict(r)
                for r in self.accruals.values()
                if r["wallet_address"] == wallet and r["status"] in ("pending", "claimed")
            ]
        self._sleep()
        return rows

    def get_active_rates(self):
        return dict(self.rates)

    def get_controls(self):
        return self.controls

    def get_wallet(self, address):
        return self.linked.get(address)

    def get_user_by_id(self, user_id):
        return {"id": user_id, "is_active": user_id not in self.inactive_users}

    def _paused(self, asset):
        # Mirrors the SQL: only an explicit "not paused" row is not paused.
        if not isinstance(self.controls, dict):
            return True
        return (self.controls.get(asset) or {}).get("accruals_paused") is not False

    # -- the SQL functions ----------------------------------------------------
    def _account_spent(self, user_id, day, statuses, exclude_id=None):
        wallets = {w for w, r in self.linked.items() if r["user_id"] == user_id}
        return sum(
            (
                Decimal(r["credits"])
                for r in self.accruals.values()
                if r["reward_date"] == day
                and r["status"] in statuses
                and r["id"] != exclude_id
                and (
                    r.get("user_id") == user_id
                    or r.get("paid_user_id") == user_id
                    or r["wallet_address"] in wallets
                )
            ),
            Decimal(0),
        )

    def reserve_accrual(self, wallet, asset, day, basis, credits, rate, user_id, cap, budget):
        day = str(day)
        with self._date_lock:
            key = (wallet, asset, day)
            if key in self.accruals:
                return {"status": "exists", "accrual": dict(self.accruals[key])}
            if self._paused(asset):
                return {"status": "paused"}
            spent = (
                self._account_spent(user_id, day, ("pending", "claimed", "paid"))
                if user_id is not None
                else Decimal(0)
            )
            grant = min(Decimal(credits), Decimal(cap) - spent)
            if grant <= 0:
                return {"status": "over_cap"}
            committed = sum(
                (
                    Decimal(r["credits"])
                    for r in self.accruals.values()
                    if r["reward_date"] == day and r["status"] in ("pending", "claimed", "paid")
                ),
                Decimal(0),
            )
            self._sleep()
            if committed + grant > Decimal(budget):
                return {"status": "budget_exhausted"}
            with self._mutex:
                row = {
                    "id": self._id,
                    "wallet_address": wallet,
                    "asset": asset,
                    "reward_date": day,
                    "usd_basis": str(basis),
                    "credits": str(grant),
                    "status": "pending",
                    "user_id": user_id,
                    "paid_user_id": None,
                }
                self._id += 1
                self.accruals[key] = row
            self.events.append(f"create:{asset}:{wallet}")
            return {"status": "created", "accrual": dict(row), "capped": grant < Decimal(credits)}

    def claim_accrual(self, accrual_id, user_id, cap):
        with self._date_lock:
            row = next((r for r in self.accruals.values() if r["id"] == accrual_id), None)
            if row is None:
                return {"status": "not_found"}
            if row["status"] in ("paid", "void", "claimed"):
                return {"status": row["status"], "accrual": dict(row)}
            if self._paused(row["asset"]):
                return {"status": "paused"}
            linked = self.linked.get(row["wallet_address"]) or {}
            if user_id is None or linked.get("user_id") != user_id:
                return {"status": "not_linked"}
            if user_id in self.inactive_users:
                return {"status": "inactive_user"}
            spent = self._account_spent(
                user_id, row["reward_date"], ("claimed", "paid"), exclude_id=row["id"]
            )
            self._sleep()
            if Decimal(row["credits"]) > Decimal(cap) - spent:
                row["status"] = "void"
                return {"status": "void", "accrual": dict(row)}
            row["status"] = "claimed"
            row["paid_user_id"] = user_id
            return {"status": "claimed", "accrual": dict(row)}

    def release_claim(self, accrual_id, user_id):
        with self._mutex:
            for row in self.accruals.values():
                if (
                    row["id"] == accrual_id
                    and row["status"] == "claimed"
                    and row["paid_user_id"] == user_id
                ):
                    row["status"] = "pending"
                    row["paid_user_id"] = None
                    return True
        return False

    def mark_accrual_paid(self, accrual_id, request_id):
        with self._mutex:
            for row in self.accruals.values():
                if row["id"] == accrual_id and row["status"] == "claimed":
                    row["status"] = "paid"
                    row["ledger_request_id"] = request_id
                    return dict(row)
        return None

    def add_credits_to_user(self, **kwargs):
        if self.fail_credit:
            raise RuntimeError("ledger down")
        with self._mutex:
            if kwargs["request_id"] in self.ledger:  # the ledger's unique request_id
                return
            self.ledger.add(kwargs["request_id"])
            self.events.append(f"credit:{kwargs['request_id']}")
            self.credit_calls.append(kwargs)

    def paid_total(self, user_id=None):
        return sum(
            (
                Decimal(str(c["credits"]))
                for c in self.credit_calls
                if user_id is None or c["user_id"] == user_id
            ),
            Decimal(0),
        )


@pytest.fixture
def db(monkeypatch):
    fake = FakeDB()
    for name in (
        "list_measured_pairs_for_date",
        "get_measurements_for_date",
        "get_accrual",
        "reserve_accrual",
        "claim_accrual",
        "release_claim",
        "mark_accrual_paid",
        "list_pending_accruals_since",
        "list_pending_accruals",
        "get_active_rates",
        "get_controls",
        "get_wallet",
        "get_user_by_id",
        "add_credits_to_user",
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
    db.controls = {"eth": {"accruals_paused": True}, "ada": {"accruals_paused": False}}
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
    # Reserved for the account (claimed) but not paid; the retry pays it to
    # that same account, idempotent on the ledger request_id.
    assert db.accruals[(W1, "eth", DAY_STR)]["status"] == "claimed"
    db.fail_credit = False
    summary = rewards.run_delegation_accruals_once(DAY)
    assert summary["paid"] == 1
    assert db.accruals[(W1, "eth", DAY_STR)]["status"] == "paid"


def test_unlinked_wallet_accrues_pending_and_is_paid_on_link(db):
    db.measurements[(W1, "eth")] = [Decimal(1000)] * 2
    summary = rewards.run_delegation_accruals_once(DAY)
    assert summary["pending"] == 1
    assert db.credit_calls == []
    _link(db, W1, 9)
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


# -- security regressions: cap / budget bypasses ----------------------------------

W3 = "0x" + "3" * 40


def test_a_cap_is_per_account_across_n_wallets_and_both_assets(db):
    """(a) N wallets x 2 assets for one account still earn one cap."""
    wallets = ["0x" + str(i) * 40 for i in range(1, 7)]
    for w in wallets:
        _link(db, w, 7)
        db.measurements[(w, "eth")] = [Decimal(5000)] * 2
    _link(db, ADA1, 7)
    db.measurements[(ADA1, "ada")] = [Decimal(5000)] * 2
    rewards.run_delegation_accruals_once(DAY)
    assert db.paid_total(7) == Decimal(5)
    live = [r for r in db.accruals.values() if r["status"] in ("pending", "claimed", "paid")]
    assert sum(Decimal(r["credits"]) for r in live) == Decimal(5)


def test_b_pending_paid_on_link_is_capped_at_pay_time(db):
    """(b) Accruals decided while unlinked are each capped alone; paying them
    on link must still respect the account's cap for that date."""
    for w in (W1, W2, W3):
        db.measurements[(w, "eth")] = [Decimal(4000)] * 2  # 4 credits each, unlinked
    rewards.run_delegation_accruals_once(DAY)
    assert db.paid_total() == 0
    for w in (W1, W2, W3):
        _link(db, w, 9)
        rewards.pay_pending_delegation_for_wallet(w, 9)
    assert db.paid_total(9) == Decimal(4)
    rewards.run_delegation_accruals_once(DAY)  # the retry sweep must not top it up
    assert db.paid_total(9) == Decimal(4)


def test_c_same_stake_relinked_to_another_account_pays_once(db):
    """(c) One wallet-asset-day is one accrual, attributed to one account: a
    wallet paid to A, unlinked and relinked to B, never pays again."""
    _link(db, W1, 1)
    db.measurements[(W1, "eth")] = [Decimal(3000)] * 2
    rewards.run_delegation_accruals_once(DAY)
    assert db.paid_total(1) == Decimal(3)
    del db.linked[W1]
    _link(db, W1, 2)
    rewards.pay_pending_delegation_for_wallet(W1, 2)
    rewards.run_delegation_accruals_once(DAY)
    assert db.paid_total(2) == 0
    assert db.paid_total() == Decimal(3)


def test_c_moved_wallet_counts_against_the_new_account_too(db):
    """(c) B inherits a wallet already paid to A for the date: that day's
    credits count against B's cap as well, so B cannot add a full cap on top
    from another wallet."""
    _link(db, W1, 1)
    db.measurements[(W1, "eth")] = [Decimal(4000)] * 2
    rewards.run_delegation_accruals_once(DAY)
    del db.linked[W1]
    _link(db, W1, 2)
    _link(db, W2, 2)
    db.measurements[(W2, "eth")] = [Decimal(5000)] * 2
    rewards.run_delegation_accruals_once(DAY)
    assert db.paid_total(2) == Decimal(1)


def test_d_concurrent_runs_and_pay_on_link_never_exceed_cap_or_budget(db, monkeypatch):
    """(d) Two accrual runs plus pay-on-link racing on the same date. The
    reads are slowed down to widen any read-then-write window; the cap and
    budget hold because the decision lives in the atomic reserve/claim."""
    monkeypatch.setattr(rewards.Config, "DELEGATION_GLOBAL_DAILY_BUDGET_CREDITS", Decimal("12"))
    db.race_delay = 0.002
    for i in range(12):
        w = f"0x{i:040x}"
        _link(db, w, 100 + i % 3)  # three accounts, four wallets each
        db.measurements[(w, "eth")] = [Decimal(3000)] * 2
    unlinked = [f"0x{0xF00 + i:040x}" for i in range(4)]
    for w in unlinked:
        db.measurements[(w, "eth")] = [Decimal(3000)] * 2

    errors: list[BaseException] = []

    def guarded(fn, *args):
        try:
            fn(*args)
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    threads = [
        threading.Thread(target=guarded, args=(rewards.run_delegation_accruals_once, DAY))
        for _ in range(3)
    ]

    def link_then_pay(w):
        _link(db, w, 100)
        rewards.pay_pending_delegation_for_wallet(w, 100)

    for w in unlinked:
        threads.append(threading.Thread(target=guarded, args=(link_then_pay, w)))
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    for account in (100, 101, 102):
        assert db.paid_total(account) <= Decimal(5)
    live = [r for r in db.accruals.values() if r["status"] in ("pending", "claimed", "paid")]
    assert sum(Decimal(r["credits"]) for r in live) <= Decimal(12)
    assert db.paid_total() <= Decimal(12)
    assert len(db.ledger) == len(db.credit_calls)  # no double grant


def test_e_budget_is_global_across_assets(db, monkeypatch):
    """(e) One budget for the date across ETH and ADA, not one per asset."""
    monkeypatch.setattr(rewards.Config, "DELEGATION_GLOBAL_DAILY_BUDGET_CREDITS", Decimal("6"))
    _link(db, W1, 1)
    _link(db, ADA1, 2)
    db.measurements[(W1, "eth")] = [Decimal(4000)] * 2  # 4 credits
    db.measurements[(ADA1, "ada")] = [Decimal(2000)] * 2  # 4 credits at the ADA rate
    summary = rewards.run_delegation_accruals_once(DAY)
    assert db.paid_total() == Decimal(4)
    assert summary["skipped"]["budget_exhausted"] == 1


def test_e_pause_is_rechecked_at_pay_time(db):
    """(e) A pending accrual whose asset was paused after it was decided is
    not paid -- not by the run's retry sweep, not on link."""
    db.measurements[(W1, "eth")] = [Decimal(1000)] * 2
    rewards.run_delegation_accruals_once(DAY)  # unlinked -> pending
    db.controls = {"eth": {"accruals_paused": True}, "ada": {"accruals_paused": False}}
    _link(db, W1, 5)
    rewards.pay_pending_delegation_for_wallet(W1, 5)
    rewards.run_delegation_accruals_once(DAY)
    assert db.paid_total() == 0
    assert db.accruals[(W1, "eth", DAY_STR)]["status"] == "pending"


def test_a_two_evm_wallets_and_a_stake_address_share_one_cap(db):
    """(a) 2 EVM wallets + 1 Cardano stake address on one account, both
    assets, one reward date: one cap in total."""
    _link(db, W1, 7)
    _link(db, W2, 7)
    _link(db, ADA1, 7)
    db.measurements[(W1, "eth")] = [Decimal(3000)] * 2  # 3 credits
    db.measurements[(W2, "eth")] = [Decimal(3000)] * 2  # 3 credits
    db.measurements[(ADA1, "ada")] = [Decimal(1500)] * 2  # 3 credits at the ADA rate
    rewards.run_delegation_accruals_once(DAY)
    assert db.paid_total(7) == Decimal(5)
    rewards.run_delegation_accruals_once(DAY)
    assert db.paid_total(7) == Decimal(5)


# -- fail-closed pause / budget reads ----------------------------------------------


@pytest.mark.parametrize(
    "controls",
    [
        None,  # read failed
        {},  # read returned nothing
        {"eth": {}, "ada": {}},  # rows without an explicit flag
        {"ada": {"accruals_paused": False}},  # ETH row missing
        {"eth": {"accruals_paused": None}, "ada": {"accruals_paused": None}},
    ],
)
def test_f_unknown_pause_state_pays_nothing(db, controls):
    _link(db, W1, 7)
    db.measurements[(W1, "eth")] = [Decimal(1000)] * 2
    db.controls = controls
    rewards.run_delegation_accruals_once(DAY)
    rewards.pay_pending_delegation_for_wallet(W1, 7)
    assert db.credit_calls == []


def test_f_pause_flag_read_failing_after_reserve_blocks_the_payout(db, monkeypatch):
    """The pause is re-read inside the claim: a controls read that starts
    failing between reserve and pay still pays nothing."""
    _link(db, W1, 7)
    db.measurements[(W1, "eth")] = [Decimal(1000)] * 2
    real_reserve = db.reserve_accrual

    def reserve_then_break(*args):
        result = real_reserve(*args)
        db.controls = None
        return result

    monkeypatch.setattr(rewards, "reserve_accrual", reserve_then_break)
    rewards.run_delegation_accruals_once(DAY)
    assert db.credit_calls == []
    assert db.accruals[(W1, "eth", DAY_STR)]["status"] == "pending"


def test_f_budget_or_reserve_failure_pays_nothing(db, monkeypatch):
    """The cap/budget read lives in the reserve; if it fails, nothing is
    reserved and nothing is paid."""
    _link(db, W1, 7)
    db.measurements[(W1, "eth")] = [Decimal(1000)] * 2
    monkeypatch.setattr(rewards, "reserve_accrual", lambda *a: None)
    summary = rewards.run_delegation_accruals_once(DAY)
    assert db.credit_calls == [] and db.accruals == {}
    assert summary["errors"] == 1


def test_f_claim_failure_pays_nothing(db, monkeypatch):
    db.measurements[(W1, "eth")] = [Decimal(1000)] * 2
    rewards.run_delegation_accruals_once(DAY)  # unlinked -> pending
    _link(db, W1, 7)
    monkeypatch.setattr(rewards, "claim_accrual", lambda *a: None)
    rewards.pay_pending_delegation_for_wallet(W1, 7)
    rewards.run_delegation_accruals_once(DAY)
    assert db.credit_calls == []
    assert db.accruals[(W1, "eth", DAY_STR)]["status"] == "pending"


# -- fail-closed authorization -----------------------------------------------------


def _boom(*_a, **_k):
    raise RuntimeError("user_wallets lookup timed out")


@pytest.mark.parametrize(
    "lookup",
    [
        _boom,  # lookup raised
        lambda a: None,  # unlinked / failed (db layer returns None on error)
        lambda a: {"wallet_address": a, "user_id": 7, "is_active": False},  # inactive
        lambda a: {"wallet_address": a, "user_id": None},  # no account
        lambda a: {"wallet_address": a, "user_id": "7"},  # malformed id
        lambda a: {"wallet_address": a},  # missing id
    ],
)
def test_g_wallet_to_account_lookup_problems_never_credit(db, monkeypatch, lookup):
    _link(db, W1, 7)  # what the SQL side would see
    db.measurements[(W1, "eth")] = [Decimal(1000)] * 2
    monkeypatch.setattr(rewards, "get_wallet", lookup)
    rewards.run_delegation_accruals_once(DAY)
    assert db.credit_calls == []


def test_g_payout_to_an_account_that_does_not_own_the_wallet_is_refused(db):
    """pay-on-link called for account 9 while the wallet is linked to 8 (or
    to nobody): the SQL claim refuses, nothing is credited."""
    db.measurements[(W1, "eth")] = [Decimal(1000)] * 2
    rewards.run_delegation_accruals_once(DAY)
    rewards.pay_pending_delegation_for_wallet(W1, 9)  # not linked at all
    _link(db, W1, 8)
    rewards.pay_pending_delegation_for_wallet(W1, 9)  # linked to someone else
    assert db.credit_calls == []
    rewards.pay_pending_delegation_for_wallet(W1, 8)
    assert [c["user_id"] for c in db.credit_calls] == [8]


def test_g_unlink_racing_the_payout_releases_the_claim_and_pays_nothing(db, monkeypatch):
    """The wallet moves to another account between the claim and the credit
    write: the pre-write re-verification releases the claim, no credit."""
    db.measurements[(W1, "eth")] = [Decimal(1000)] * 2
    rewards.run_delegation_accruals_once(DAY)
    _link(db, W1, 7)
    real_claim = db.claim_accrual

    def claim_then_move(accrual_id, user_id, cap):
        result = real_claim(accrual_id, user_id, cap)
        _link(db, W1, 8)  # moved to account 8 right after the claim
        return result

    monkeypatch.setattr(rewards, "claim_accrual", claim_then_move)
    rewards.pay_pending_delegation_for_wallet(W1, 7)
    assert db.credit_calls == []
    row = db.accruals[(W1, "eth", DAY_STR)]
    assert row["status"] == "pending" and row["paid_user_id"] is None
    # The new owner can be paid once, through a fresh claim under its own cap.
    monkeypatch.setattr(rewards, "claim_accrual", real_claim)
    rewards.pay_pending_delegation_for_wallet(W1, 8)
    assert [c["user_id"] for c in db.credit_calls] == [8]


def test_g_resumed_claim_is_reverified_before_paying(db):
    """A claim left by a crashed payment is only paid if its payee still owns
    the wallet; otherwise it is released."""
    _link(db, W1, 7)
    db.measurements[(W1, "eth")] = [Decimal(1000)] * 2
    db.fail_credit = True
    rewards.run_delegation_accruals_once(DAY)
    assert db.accruals[(W1, "eth", DAY_STR)]["status"] == "claimed"
    db.fail_credit = False
    del db.linked[W1]  # unlinked before the retry
    rewards.run_delegation_accruals_once(DAY)
    assert db.credit_calls == []
    assert db.accruals[(W1, "eth", DAY_STR)]["status"] == "pending"


@pytest.mark.parametrize(
    "controls,expected",
    [
        (None, {"eth", "ada"}),
        ({}, {"eth", "ada"}),
        ({"eth": {}, "ada": {"accruals_paused": False}}, {"eth"}),
        ({"eth": {"accruals_paused": None}, "ada": {"accruals_paused": False}}, {"eth"}),
        ({"eth": {"accruals_paused": False}, "ada": {"accruals_paused": False}}, set()),
        ("garbage", {"eth", "ada"}),
    ],
)
def test_f_paused_assets_fails_closed(controls, expected):
    assert rewards.paused_assets(controls) == expected


def test_g_resolve_user_id_fails_closed(monkeypatch):
    monkeypatch.setattr(rewards, "get_wallet", _boom)
    assert rewards._resolve_user_id(W1) is None
    for row in (
        None,
        {"user_id": 7, "is_active": False},
        {"user_id": "7"},
        {"user_id": True},
        {},
    ):
        monkeypatch.setattr(rewards, "get_wallet", lambda a, row=row: row)
        assert rewards._resolve_user_id(W1) is None
    monkeypatch.setattr(rewards, "get_wallet", lambda a: {"user_id": 7})
    assert rewards._resolve_user_id(W1) == 7


def test_g_inactive_user_is_never_credited(db):
    """A deactivated account is refused by the SQL claim."""
    _link(db, W1, 7)
    db.inactive_users.add(7)
    db.measurements[(W1, "eth")] = [Decimal(1000)] * 2
    rewards.run_delegation_accruals_once(DAY)
    rewards.pay_pending_delegation_for_wallet(W1, 7)
    assert db.credit_calls == []
    assert db.accruals[(W1, "eth", DAY_STR)]["status"] == "pending"


@pytest.mark.parametrize(
    "user_lookup",
    [
        lambda uid: {"id": uid, "is_active": False},  # deactivated after the claim
        lambda uid: None,  # deleted, or the lookup failed (db layer returns None)
        _boom,  # lookup raised
        lambda uid: "garbage",
    ],
)
def test_g_payee_account_rechecked_right_before_the_credit(db, monkeypatch, user_lookup):
    """The SQL claim passed, then the account check right before the credit
    write fails: the claim is released and nothing is credited."""
    _link(db, W1, 7)
    db.measurements[(W1, "eth")] = [Decimal(1000)] * 2
    monkeypatch.setattr(rewards, "get_user_by_id", user_lookup)
    rewards.run_delegation_accruals_once(DAY)
    assert db.credit_calls == []
    row = db.accruals[(W1, "eth", DAY_STR)]
    assert row["status"] == "pending" and row["paid_user_id"] is None


def test_g_none_user_id_is_never_claimed_or_paid(db):
    db.measurements[(W1, "eth")] = [Decimal(1000)] * 2
    rewards.run_delegation_accruals_once(DAY)  # unlinked: user_id None
    row = db.accruals[(W1, "eth", DAY_STR)]
    assert db.claim_accrual(row["id"], None, Decimal(5))["status"] == "not_linked"
    assert db.credit_calls == []
