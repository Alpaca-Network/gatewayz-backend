"""Tests for src.services.delegation.attribution -- the v3 reward-share job.

Invariants: a wallet is attributed revenue x stake / total (ETH: lowest-of-day
the day before the revenue row; ADA: the epoch's active stake); the discount
is reward_share of that, truncated to 6 dp; a period never attributes more
than its revenue; pending before grant, idempotent on re-run; only a linked,
active account is granted; a paused (or unknown-pause) asset attributes and
grants nothing.
"""

from __future__ import annotations

import threading
import uuid
from datetime import date, timedelta
from decimal import ROUND_DOWN, Decimal

import pytest

import src.services.delegation.attribution as attribution
import src.services.delegation.rewards as rewards
from src.services.delegation import koios

TODAY = date(2026, 10, 11)
REV_DAY = "2026-10-10"  # an ETH revenue row: rewards earned over 2026-10-09
MEASURE_DAY = date(2026, 10, 9)
W1 = "0x" + "1" * 40
W2 = "0x" + "2" * 40
ADA1 = "stake1u" + "a" * 50
ADA2 = "stake1u" + "b" * 50
WEI = 10**18
LOVELACE = 10**6


class FakeDB:
    """In-memory stand-in for src.db.delegation + user_wallets + users.

    reserve_attribution / grant_attribution mirror the SQL functions in
    20261011120000_delegation_reward_share_discount.sql (section 6),
    including the per-date lock, so the module under test can only stay
    inside the ceilings if it leaves them to the SQL."""

    def __init__(self):
        self.revenue: dict[str, list[dict]] = {"eth": [], "ada": []}
        self.amounts: dict[tuple[str, date], dict[str, list[int]] | None] = {}
        self.attributions: dict[tuple[str, str, str], dict] = {}
        self.controls: dict | None = {
            "eth": {"accruals_paused": False},
            "ada": {"accruals_paused": False},
        }
        self.linked: dict[str, dict] = {}
        self.inactive_users: set[int] = set()
        self.balances: dict[int, Decimal] = {}
        self.ledger: dict[str, dict] = {}
        self.wallet_rows: list[dict] = []
        self._id = 1
        self._lock = threading.RLock()

    # -- plain reads ----------------------------------------------------------
    def list_revenue_since(self, asset, floor):
        return [dict(r) for r in self.revenue[asset] if r["revenue_date"] >= str(floor)]

    def get_measurement_amounts_for_date(self, asset, day):
        value = self.amounts.get((asset, day), {})
        return None if value is None else {k: list(v) for k, v in value.items()}

    def get_attribution(self, wallet, asset, period_key):
        row = self.attributions.get((wallet.lower(), asset, period_key))
        return dict(row) if row else None

    def list_pending_attributions(self, wallet_address=None, min_reward_date=None):
        return [
            dict(r)
            for r in self.attributions.values()
            if r["status"] == "pending"
            and (wallet_address is None or r["wallet_address"] == wallet_address.lower())
        ]

    def get_controls(self):
        return self.controls

    def get_wallet(self, address):
        return self.linked.get(address)

    def get_user_by_id(self, user_id):
        return {"id": user_id, "is_active": user_id not in self.inactive_users}

    def list_all_wallets(self):
        return list(self.wallet_rows)

    def _paused(self, asset):
        if not isinstance(self.controls, dict):
            return True
        return (self.controls.get(asset) or {}).get("accruals_paused") is not False

    # -- the SQL functions ----------------------------------------------------
    def _spent(self, user_id, reward_date, statuses, exclude=None):
        wallets = {w for w, r in self.linked.items() if r["user_id"] == user_id}
        return sum(
            (
                r["discount_usd"]
                for r in self.attributions.values()
                if r["reward_date"] == reward_date
                and r["status"] in statuses
                and r["id"] != exclude
                and (r["user_id"] == user_id or r["wallet_address"] in wallets)
            ),
            Decimal(0),
        )

    def reserve_attribution(
        self,
        wallet,
        asset,
        period_key,
        reward_date,
        user_id,
        stake,
        total,
        attributed,
        share,
        request_id,
        cap,
        budget,
    ):
        with self._lock:
            key = (wallet.lower(), asset, period_key)
            if key in self.attributions:
                return {"status": "exists", "attribution": dict(self.attributions[key])}
            uuid.UUID(request_id)  # the SQL column is a uuid
            if self._paused(asset):
                return {"status": "paused"}
            rev = next(
                (r for r in self.revenue[asset] if r["period_key"] == period_key),
                None,
            )
            if rev is None or Decimal(rev["revenue_usd"]) <= 0:
                return {"status": "no_revenue"}
            already = sum(
                (
                    r["attributed_usd"]
                    for r in self.attributions.values()
                    if r["asset"] == asset
                    and r["period_key"] == period_key
                    and r["status"] in ("pending", "granted")
                ),
                Decimal(0),
            )
            v_attr = min(Decimal(attributed), Decimal(rev["revenue_usd"]) - already)
            if v_attr <= 0:
                return {"status": "revenue_exhausted"}
            v_share = min(max(Decimal(share), Decimal(0)), Decimal(1))
            v_disc = (v_attr * v_share).quantize(Decimal("0.000001"), rounding=ROUND_DOWN)
            spent = (
                self._spent(user_id, reward_date, ("pending", "granted"))
                if user_id is not None
                else Decimal(0)
            )
            grant = min(v_disc, cap - spent).quantize(Decimal("0.000001"), rounding=ROUND_DOWN)
            if grant <= 0:
                return {"status": "over_cap"}
            committed = sum(
                (
                    r["discount_usd"]
                    for r in self.attributions.values()
                    if r["reward_date"] == reward_date and r["status"] in ("pending", "granted")
                ),
                Decimal(0),
            )
            if committed + grant > budget:
                return {"status": "budget_exhausted"}
            row = {
                "id": self._id,
                "wallet_address": wallet.lower(),
                "asset": asset,
                "period_key": period_key,
                "reward_date": reward_date,
                "user_id": user_id,
                "stake_native": Decimal(stake),
                "stake_total_native": Decimal(total),
                "attributed_usd": v_attr,
                "reward_share": v_share,
                "discount_usd": grant,
                "status": "pending",
                "request_id": request_id,
            }
            self._id += 1
            self.attributions[key] = row
            return {
                "status": "created",
                "attribution": dict(row),
                "capped": grant < v_disc or v_attr < Decimal(attributed),
            }

    def grant_attribution(self, attribution_id, user_id, cap):
        with self._lock:
            row = next((r for r in self.attributions.values() if r["id"] == attribution_id), None)
            if row is None:
                return {"status": "not_found"}
            if row["status"] == "granted":
                return {"status": "already", "attribution": dict(row)}
            if row["status"] == "void":
                return {"status": "void", "attribution": dict(row)}
            if self._paused(row["asset"]):
                return {"status": "paused"}
            if (self.linked.get(row["wallet_address"]) or {}).get("user_id") != user_id:
                return {"status": "not_linked"}
            if user_id in self.inactive_users:
                return {"status": "inactive_user"}
            spent = self._spent(user_id, row["reward_date"], ("granted",), exclude=row["id"])
            if row["discount_usd"] > cap - spent:
                row["status"] = "void"
                return {"status": "void", "attribution": dict(row)}
            # discount_grant, idempotent on request_id
            if row["request_id"] not in self.ledger:
                self.ledger[row["request_id"]] = {"user_id": user_id, "amount": row["discount_usd"]}
                self.balances[user_id] = (
                    self.balances.get(user_id, Decimal(0)) + row["discount_usd"]
                )
            row["status"] = "granted"
            row["user_id"] = user_id
            return {"status": "granted", "attribution": dict(row)}


@pytest.fixture
def db(monkeypatch):
    fake = FakeDB()
    for name in (
        "list_revenue_since",
        "get_measurement_amounts_for_date",
        "get_attribution",
        "list_pending_attributions",
        "get_controls",
        "reserve_attribution",
        "grant_attribution",
        "list_all_wallets",
    ):
        monkeypatch.setattr(attribution, name, getattr(fake, name))
    monkeypatch.setattr(rewards, "get_wallet", fake.get_wallet)
    monkeypatch.setattr(rewards, "get_user_by_id", fake.get_user_by_id)
    cfg = attribution.Config
    monkeypatch.setattr(cfg, "DELEGATED_STAKING_ENABLED", True)
    monkeypatch.setattr(cfg, "DELEGATION_PAYOUT_MODEL", "reward_share")
    monkeypatch.setattr(cfg, "DELEGATION_REWARD_SHARE", Decimal("0.95"))
    monkeypatch.setattr(cfg, "DELEGATION_DAILY_CAP_CREDITS", Decimal("1000"))
    monkeypatch.setattr(cfg, "DELEGATION_GLOBAL_DAILY_BUDGET_CREDITS", Decimal("10000"))
    monkeypatch.setattr(cfg, "DELEGATION_MEASUREMENTS_PER_DAY", 4)
    monkeypatch.setattr(cfg, "DELEGATION_MIN_MEASUREMENTS_PER_DAY", 2)
    monkeypatch.setattr(rewards, "eth_configured", lambda: True)
    monkeypatch.setattr(rewards, "ada_configured", lambda: False)
    monkeypatch.setattr(attribution, "cardano_pool_id", lambda: "pool1test")
    return fake


def _link(db, address, user_id, cardano=False):
    row = {"wallet_address": address, "user_id": user_id}
    if cardano:
        row["chain_namespace"] = "cip34"
    db.linked[address] = row
    db.wallet_rows.append(row)


def _eth_revenue(db, usd, total_eth, day=REV_DAY):
    db.revenue["eth"].append(
        {
            "asset": "eth",
            "revenue_date": day,
            "period_key": day,
            "revenue_native": "0.01",
            "revenue_usd": str(usd),
            "stake_total_native": None if total_eth is None else str(total_eth),
        }
    )


# -- the math -------------------------------------------------------------------------


class TestAttributeMath:
    def test_pro_rata_then_share_truncated_to_6dp(self):
        attributed, discount = attribution.attribute(
            Decimal("30"), Decimal("1"), Decimal("3"), Decimal("0.95")
        )
        assert attributed == Decimal("10.000000000000000000")
        assert discount == Decimal("9.500000")

    def test_rounds_down_never_up(self):
        # 10 / 3 = 3.333... -> x 0.95 = 3.1666...; truncated, never rounded up.
        _, discount = attribution.attribute(Decimal(10), Decimal(1), Decimal(3), Decimal("0.95"))
        assert discount == Decimal("3.166666")

    def test_stake_above_total_is_capped_at_the_whole_revenue(self):
        attributed, _ = attribution.attribute(Decimal(30), Decimal(5), Decimal(3), Decimal(1))
        assert attributed == Decimal(30)

    @pytest.mark.parametrize(
        "rev,stake,total", [(0, 1, 1), (10, 0, 1), (10, 1, 0), (-1, 1, 1), (10, -1, 1)]
    )
    def test_non_positive_inputs_attribute_nothing(self, rev, stake, total):
        assert attribution.attribute(
            Decimal(rev), Decimal(stake), Decimal(total), Decimal("0.95")
        ) == (Decimal(0), Decimal(0))

    def test_share_is_clamped(self):
        _, discount = attribution.attribute(Decimal(10), Decimal(1), Decimal(1), Decimal(2))
        assert discount == Decimal("10.000000")

    def test_request_id_is_a_deterministic_uuid(self):
        a = attribution.request_id_for("eth", W1.upper(), REV_DAY)
        assert a == attribution.request_id_for("eth", W1, REV_DAY)
        assert uuid.UUID(a)
        assert a != attribution.request_id_for("eth", W1, "2026-10-11")
        assert attribution.request_key_for("ada", ADA1, "epoch:500") == (
            f"delegation_share:ada:{ADA1}:epoch:500"
        )


# -- ETH ------------------------------------------------------------------------------


class TestEth:
    def test_pro_rata_on_lowest_of_the_previous_day(self, db):
        _link(db, W1, 7)
        _link(db, W2, 8)
        # W1's lowest is 1 ETH (not 3); W2 holds 3 ETH all day; total staked 4 ETH.
        db.amounts[("eth", MEASURE_DAY)] = {
            W1: [3 * WEI, 1 * WEI, 2 * WEI],
            W2: [3 * WEI, 3 * WEI],
        }
        _eth_revenue(db, 40, 4)
        summary = attribution.run_delegation_attribution_once(TODAY)
        assert summary["granted"] == 2
        # W1: 40 x 1/4 = 10 -> 9.5; W2: 40 x 3/4 = 30 -> 28.5.
        assert db.balances == {7: Decimal("9.500000"), 8: Decimal("28.500000")}
        row = db.attributions[(W1, "eth", REV_DAY)]
        assert row["stake_native"] == Decimal(1) and row["reward_date"] == REV_DAY
        assert summary["discount_granted"]["eth"] == "38.000000"

    def test_too_few_measurements_attributes_nothing(self, db):
        _link(db, W1, 7)
        db.amounts[("eth", MEASURE_DAY)] = {W1: [5 * WEI]}  # 1 < 2 required
        _eth_revenue(db, 40, 4)
        attribution.run_delegation_attribution_once(TODAY)
        assert db.attributions == {} and db.balances == {}

    def test_denominator_never_below_the_measured_stakes(self, db):
        # The reading's total (2 ETH) is below what we measured (1 + 3): the
        # wallets still split the revenue 1:3, and never more than all of it.
        _link(db, W1, 7)
        _link(db, W2, 8)
        db.amounts[("eth", MEASURE_DAY)] = {W1: [WEI, WEI], W2: [3 * WEI, 3 * WEI]}
        _eth_revenue(db, 40, 2)
        attribution.run_delegation_attribution_once(TODAY)
        total = sum(r["attributed_usd"] for r in db.attributions.values())
        assert total == Decimal(40)
        assert db.balances[7] == Decimal("9.500000")

    def test_missing_denominator_skips_the_period(self, db):
        _link(db, W1, 7)
        db.amounts[("eth", MEASURE_DAY)] = {W1: [WEI, WEI]}
        _eth_revenue(db, 40, None)
        summary = attribution.run_delegation_attribution_once(TODAY)
        assert summary["skipped"]["no_denominator"] == 1 and db.attributions == {}

    def test_unreadable_measurements_skip_the_period_not_zero_it(self, db):
        _link(db, W1, 7)
        db.amounts[("eth", MEASURE_DAY)] = None
        _eth_revenue(db, 40, 4)
        summary = attribution.run_delegation_attribution_once(TODAY)
        assert summary["skipped"]["unreadable_period"] == 1 and db.attributions == {}

    def test_zero_revenue_period_writes_nothing(self, db):
        _link(db, W1, 7)
        db.amounts[("eth", MEASURE_DAY)] = {W1: [WEI, WEI]}
        _eth_revenue(db, 0, 4)
        summary = attribution.run_delegation_attribution_once(TODAY)
        assert summary["skipped"]["zero_revenue"] == 1 and db.attributions == {}


# -- ADA ------------------------------------------------------------------------------


class TestAda:
    @pytest.fixture
    def ada(self, db, monkeypatch):
        monkeypatch.setattr(rewards, "eth_configured", lambda: False)
        monkeypatch.setattr(rewards, "ada_configured", lambda: True)
        calls = []

        def history(pool_id, epoch):
            calls.append((pool_id, epoch))
            return [
                {"stake_address": ADA1, "amount": str(1000 * LOVELACE), "epoch_no": epoch},
                {"stake_address": ADA2, "amount": str(3000 * LOVELACE), "epoch_no": epoch},
                # Not linked to any account: earns nobody anything.
                {"stake_address": "stake1uzzz", "amount": str(6000 * LOVELACE)},
            ]

        monkeypatch.setattr(koios, "list_pool_delegators_history", history)
        db.revenue["ada"].append(
            {
                "asset": "ada",
                "revenue_date": REV_DAY,
                "period_key": "epoch:500",
                "revenue_native": "100",
                "revenue_usd": "50",
                "stake_total_native": "10000",
            }
        )
        return calls

    def test_per_epoch_active_stake_over_pool_active_stake(self, db, ada):
        _link(db, ADA1, 7, cardano=True)
        _link(db, ADA2, 8, cardano=True)
        attribution.run_delegation_attribution_once(TODAY)
        assert ada == [("pool1test", 500)]
        # 50 x 1000/10000 = 5 -> 4.75; 50 x 3000/10000 = 15 -> 14.25. The
        # unlinked delegator's 30 stays with Gatewayz.
        assert db.balances == {7: Decimal("4.750000"), 8: Decimal("14.250000")}
        assert db.attributions[(ADA1, "ada", "epoch:500")]["stake_native"] == Decimal(1000)

    def test_missing_total_falls_back_to_pool_history(self, db, ada, monkeypatch):
        db.revenue["ada"][0]["stake_total_native"] = None
        monkeypatch.setattr(
            koios,
            "get_pool_history",
            lambda pool: [{"epoch_no": 500, "active_stake": str(20000 * LOVELACE)}],
        )
        _link(db, ADA1, 7, cardano=True)
        attribution.run_delegation_attribution_once(TODAY)
        assert db.balances == {7: Decimal("2.375000")}  # 50 x 1000/20000 x 0.95

    def test_koios_failure_skips_the_epoch(self, db, ada, monkeypatch):
        def boom(pool_id, epoch):
            raise koios.KoiosError("Koios pool_delegators_history failed: ConnectionError")

        monkeypatch.setattr(koios, "list_pool_delegators_history", boom)
        _link(db, ADA1, 7, cardano=True)
        summary = attribution.run_delegation_attribution_once(TODAY)
        assert summary["skipped"]["unreadable_period"] == 1 and db.attributions == {}


# -- payout safety ----------------------------------------------------------------------


class TestPayout:
    def _setup(self, db, usd=40):
        _link(db, W1, 7)
        db.amounts[("eth", MEASURE_DAY)] = {W1: [WEI, WEI]}
        _eth_revenue(db, usd, 4)

    def test_rerun_is_idempotent(self, db):
        self._setup(db)
        attribution.run_delegation_attribution_once(TODAY)
        attribution.run_delegation_attribution_once(TODAY)
        second = attribution.run_delegation_attribution_once(TODAY)
        assert len(db.attributions) == 1 and len(db.ledger) == 1
        assert db.balances == {7: Decimal("9.500000")}
        assert second["already"] == 1 and second["granted"] == 0

    def test_disabled_and_allowance_model_do_nothing(self, db, monkeypatch):
        self._setup(db)
        monkeypatch.setattr(attribution.Config, "DELEGATION_PAYOUT_MODEL", "allowance")
        assert attribution.run_delegation_attribution_once(TODAY)["skipped"] == "payout_model"
        monkeypatch.setattr(attribution.Config, "DELEGATION_PAYOUT_MODEL", "reward_share")
        monkeypatch.setattr(attribution.Config, "DELEGATED_STAKING_ENABLED", False)
        assert attribution.run_delegation_attribution_once(TODAY) == {"skipped": "disabled"}
        assert db.attributions == {}

    @pytest.mark.parametrize(
        "controls",
        [None, {}, {"eth": {}}, {"eth": {"accruals_paused": True}}, {"eth": {"x": 1}}],
    )
    def test_paused_or_unreadable_controls_fail_closed(self, db, controls):
        self._setup(db)
        db.controls = controls
        summary = attribution.run_delegation_attribution_once(TODAY)
        assert db.attributions == {} and db.balances == {}
        assert "eth" in summary["paused_assets"]

    def test_pause_after_reserve_blocks_the_grant(self, db):
        self._setup(db)
        db.linked.pop(W1)  # reserve pending (no account), then pause
        attribution.run_delegation_attribution_once(TODAY)
        assert db.attributions[(W1, "eth", REV_DAY)]["status"] == "pending"
        db.controls = {"eth": {"accruals_paused": True}, "ada": {"accruals_paused": False}}
        _link(db, W1, 7)
        attribution.grant_pending_for_wallet(W1, 7)
        assert db.balances == {}

    def test_unlinked_wallet_stays_pending_then_is_granted_on_link(self, db):
        db.amounts[("eth", MEASURE_DAY)] = {W1: [WEI, WEI]}
        _eth_revenue(db, 40, 4)
        attribution.run_delegation_attribution_once(TODAY)
        # Never measured as linked now, but it was (the measurement exists):
        # pending, nothing granted.
        assert db.attributions[(W1, "eth", REV_DAY)]["status"] == "pending"
        assert db.balances == {}
        _link(db, W1, 7)
        attribution.grant_pending_for_wallet(W1, 7)
        assert db.balances == {7: Decimal("9.500000")}

    def test_inactive_account_is_never_granted(self, db):
        self._setup(db)
        db.inactive_users.add(7)
        attribution.run_delegation_attribution_once(TODAY)
        assert db.balances == {}
        assert db.attributions[(W1, "eth", REV_DAY)]["status"] == "pending"

    def test_payee_lookup_failure_never_grants(self, db, monkeypatch):
        self._setup(db)

        def boom(_):
            raise RuntimeError("db down")

        monkeypatch.setattr(rewards, "get_user_by_id", boom)
        attribution.run_delegation_attribution_once(TODAY)
        assert db.balances == {}

    def test_account_cap_applies_to_discount(self, db, monkeypatch):
        monkeypatch.setattr(attribution.Config, "DELEGATION_DAILY_CAP_CREDITS", Decimal("5"))
        self._setup(db)
        summary = attribution.run_delegation_attribution_once(TODAY)
        assert db.balances == {7: Decimal("5.000000")}
        assert summary["capped"] == 1

    def test_global_budget_stops_granting_for_the_date(self, db, monkeypatch):
        monkeypatch.setattr(
            attribution.Config, "DELEGATION_GLOBAL_DAILY_BUDGET_CREDITS", Decimal("10")
        )
        _link(db, W1, 7)
        _link(db, W2, 8)
        db.amounts[("eth", MEASURE_DAY)] = {W1: [WEI, WEI], W2: [WEI, WEI]}
        _eth_revenue(db, 40, 2)  # 19 each > 10 budget for the second
        summary = attribution.run_delegation_attribution_once(TODAY)
        assert sum(db.balances.values()) <= Decimal(10)
        assert summary["skipped"]["budget_exhausted"] >= 1
        assert summary["budget_exhausted_dates"] == [REV_DAY]

    def test_granted_never_exceeds_reward_share_of_revenue(self, db):
        _link(db, W1, 7)
        _link(db, W2, 8)
        db.amounts[("eth", MEASURE_DAY)] = {W1: [7 * WEI, 7 * WEI], W2: [3 * WEI, 3 * WEI]}
        _eth_revenue(db, Decimal("12.345678"), 10)
        attribution.run_delegation_attribution_once(TODAY)
        assert sum(db.balances.values()) <= Decimal("0.95") * Decimal("12.345678")

    def test_window_is_thirty_days(self, db):
        _link(db, W1, 7)
        old = (TODAY - timedelta(days=31)).isoformat()
        db.amounts[("eth", date.fromisoformat(old) - timedelta(days=1))] = {W1: [WEI, WEI]}
        _eth_revenue(db, 40, 4, day=old)
        attribution.run_delegation_attribution_once(TODAY)
        assert db.attributions == {}

    def test_one_failing_wallet_does_not_sink_the_run(self, db, monkeypatch):
        _link(db, W1, 7)
        _link(db, W2, 8)
        db.amounts[("eth", MEASURE_DAY)] = {W1: [WEI, WEI], W2: [WEI, WEI]}
        _eth_revenue(db, 40, 4)
        real = db.get_attribution

        def flaky(wallet, asset, period):
            if wallet == W1:
                raise RuntimeError("boom")
            return real(wallet, asset, period)

        monkeypatch.setattr(attribution, "get_attribution", flaky)
        summary = attribution.run_delegation_attribution_once(TODAY)
        assert summary["errors"] == 1 and db.balances == {8: Decimal("9.500000")}

    def test_concurrent_runs_grant_once(self, db):
        self._setup(db)
        threads = [
            threading.Thread(target=attribution.run_delegation_attribution_once, args=(TODAY,))
            for _ in range(4)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(db.ledger) == 1 and db.balances == {7: Decimal("9.500000")}


def test_pay_on_link_routes_to_attribution_in_reward_share_mode(db, monkeypatch):
    calls = []
    monkeypatch.setattr(attribution, "grant_pending_for_wallet", lambda a, u: calls.append((a, u)))
    monkeypatch.setattr(
        rewards, "list_pending_accruals", lambda a: pytest.fail("v2 accruals must not be paid")
    )
    rewards.pay_pending_delegation_for_wallet(W1, 7)
    assert calls == [(W1, 7)]


def test_v2_accrual_grants_nothing_in_reward_share_mode(db):
    assert rewards.run_delegation_accruals_once(MEASURE_DAY) == {
        "skipped": "payout_model",
        "payout_model": "reward_share",
    }


class TestShareCannotBeInflated:
    """Security review item 5."""

    def test_exit_just_before_the_reading_cannot_shrink_the_denominator(self, db):
        # W1 held 10 ETH all of 10-09 and left right before the 10-10 reading,
        # so that reading's total (10 ETH of unlinked stake) omits it. The
        # previous reading (20 ETH) bounds the period instead: W1 gets half,
        # not all, of the revenue.
        _link(db, W1, 7)
        db.amounts[("eth", MEASURE_DAY)] = {W1: [10 * WEI, 10 * WEI]}
        _eth_revenue(db, 0, 20, day="2026-10-09")
        _eth_revenue(db, 40, 10)
        attribution.run_delegation_attribution_once(TODAY)
        assert db.balances == {7: Decimal("19.000000")}  # 40 x 10/20 x 0.95

    def test_previous_reading_outside_the_window_still_counts(self, db):
        _link(db, W1, 7)
        first = (TODAY - timedelta(days=30)).isoformat()
        prev = (TODAY - timedelta(days=31)).isoformat()
        db.amounts[("eth", date.fromisoformat(first) - timedelta(days=1))] = {
            W1: [10 * WEI, 10 * WEI]
        }
        _eth_revenue(db, 0, 20, day=prev)
        _eth_revenue(db, 40, 10, day=first)
        attribution.run_delegation_attribution_once(TODAY)
        assert db.balances == {7: Decimal("19.000000")}
        assert all(k[2] != prev for k in db.attributions)

    def test_unlinked_depositors_stay_in_the_denominator(self, db):
        # 1 ETH linked out of a 100 ETH vault: 1% of revenue, the other 99%
        # (unlinked depositors) is never paid to anyone.
        _link(db, W1, 7)
        db.amounts[("eth", MEASURE_DAY)] = {W1: [WEI, WEI]}
        _eth_revenue(db, 100, 100)
        attribution.run_delegation_attribution_once(TODAY)
        assert db.balances == {7: Decimal("0.950000")}

    def test_relinking_a_wallet_to_another_account_never_pays_twice(self, db):
        _link(db, W1, 7)
        db.amounts[("eth", MEASURE_DAY)] = {W1: [WEI, WEI]}
        _eth_revenue(db, 40, 4)
        attribution.run_delegation_attribution_once(TODAY)
        db.linked[W1] = {"wallet_address": W1, "user_id": 8}  # moved to account 8
        attribution.run_delegation_attribution_once(TODAY)
        attribution.grant_pending_for_wallet(W1, 8)
        assert db.balances == {7: Decimal("9.500000")}
        assert len(db.ledger) == 1

    def test_pending_wallet_moved_before_grant_pays_only_the_new_owner_once(self, db):
        db.amounts[("eth", MEASURE_DAY)] = {W1: [WEI, WEI]}
        _eth_revenue(db, 40, 4)
        attribution.run_delegation_attribution_once(TODAY)  # unlinked -> pending
        _link(db, W1, 7)
        db.linked[W1] = {"wallet_address": W1, "user_id": 8}
        attribution.grant_pending_for_wallet(W1, 7)  # 7 no longer owns it
        attribution.grant_pending_for_wallet(W1, 8)
        attribution.grant_pending_for_wallet(W1, 8)
        assert db.balances == {8: Decimal("9.500000")}
