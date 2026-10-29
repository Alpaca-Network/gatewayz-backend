"""Tests for src.services.delegation.reconciliation -- revenue recording and
the fail-closed pause."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

import src.services.delegation.reconciliation as recon
from src.services.delegation import koios, stakewise

TODAY = date(2026, 10, 8)
RECIPIENT = "0x" + "f" * 40


@pytest.fixture
def env(monkeypatch):
    state = SimpleNamespace(
        revenue=[],
        granted={"eth": Decimal(0), "ada": Decimal(0)},
        controls={"eth": {}, "ada": {}},
        paused=[],
        alerts=[],
        shares=10 * 10**18,
        recipient=RECIPIENT,
        history=[],
        tip=660,
    )
    monkeypatch.setattr(recon.Config, "DELEGATED_STAKING_ENABLED", True)
    # v2 allowance-model comparison; reward_share is covered below.
    monkeypatch.setattr(recon.Config, "DELEGATION_PAYOUT_MODEL", "allowance")
    monkeypatch.setattr(recon.Config, "DELEGATION_INFERENCE_MARGIN", Decimal("0.20"))
    monkeypatch.setattr(recon.Config, "DELEGATION_RECONCILIATION_TOLERANCE", Decimal("0.05"))
    monkeypatch.setattr(recon.Config, "DELEGATION_RECONCILIATION_GRACE_USD", Decimal("0"))
    monkeypatch.setattr(recon, "eth_configured", lambda: True)
    monkeypatch.setattr(recon, "ada_configured", lambda: True)
    monkeypatch.setattr(recon, "cardano_pool_id", lambda: "pool1abc")
    prices = {"ethereum": Decimal(2000), "cardano": Decimal("0.5")}
    monkeypatch.setattr(
        recon,
        "get_usd_prices",
        lambda ids: {i: SimpleNamespace(price=prices[i]) for i in ids},
    )
    monkeypatch.setattr(
        stakewise,
        "read_vault_info",
        lambda: stakewise.VaultInfo(9900, state.recipient, 10**21),
    )
    monkeypatch.setattr(stakewise, "read_shares", lambda a: state.shares)
    monkeypatch.setattr(stakewise, "convert_to_assets", lambda s: s)
    monkeypatch.setattr(koios, "get_tip_epoch", lambda: state.tip)
    monkeypatch.setattr(koios, "get_pool_history", lambda p: list(state.history))

    def insert(asset, day, key, native, usd, source, raw_amount=None, stake_total_native=None):
        if any(r["asset"] == asset and r["period_key"] == key for r in state.revenue):
            return None
        row = {
            "asset": asset,
            "period_key": key,
            "revenue_native": native,
            "revenue_usd": usd,
            "source": source,
            "raw_amount": raw_amount,
            "stake_total_native": stake_total_native,
        }
        state.revenue.append(row)
        return row

    monkeypatch.setattr(recon, "insert_revenue", insert)
    monkeypatch.setattr(
        recon,
        "get_latest_revenue",
        lambda asset: next((r for r in reversed(state.revenue) if r["asset"] == asset), None),
    )
    monkeypatch.setattr(
        recon,
        "list_revenue_period_keys",
        lambda asset: {r["period_key"] for r in state.revenue if r["asset"] == asset},
    )
    monkeypatch.setattr(
        recon,
        "sum_revenue_usd",
        lambda asset, since=None: sum(
            (Decimal(r["revenue_usd"]) for r in state.revenue if r["asset"] == asset), Decimal(0)
        ),
    )
    monkeypatch.setattr(recon, "sum_granted_credits", lambda asset: state.granted[asset])
    monkeypatch.setattr(recon, "get_controls", lambda: state.controls)
    monkeypatch.setattr(
        recon, "pause_accruals", lambda asset, reason: state.paused.append(asset) or True
    )
    monkeypatch.setattr(
        recon.alerts, "alert_overspent", lambda asset, c: state.alerts.append(asset) or True
    )
    monkeypatch.setattr(recon.alerts, "alert_revenue_read_failed", lambda a, e: True)
    return state


def test_disabled_is_a_no_op(env, monkeypatch):
    monkeypatch.setattr(recon.Config, "DELEGATED_STAKING_ENABLED", False)
    assert recon.run_delegation_reconciliation_once(TODAY) == {"skipped": "disabled"}


def test_first_eth_reading_is_a_zero_baseline_then_deltas_count(env):
    first = recon.record_eth_revenue(TODAY)
    assert first["baseline"] is True and Decimal(env.revenue[0]["revenue_usd"]) == 0
    env.shares += 10**17  # 0.1 ETH of fee shares minted
    second = recon.record_eth_revenue(date(2026, 10, 9))
    assert second["baseline"] is False
    assert Decimal(env.revenue[1]["revenue_usd"]) == Decimal(200)


def test_fee_shares_moved_out_never_count_negative(env):
    recon.record_eth_revenue(TODAY)
    env.shares -= 10**18
    recon.record_eth_revenue(date(2026, 10, 9))
    assert Decimal(env.revenue[1]["revenue_usd"]) == 0


def test_changed_fee_recipient_starts_a_new_baseline(env):
    recon.record_eth_revenue(TODAY)
    env.recipient = "0x" + "e" * 40
    env.shares = 50 * 10**18
    result = recon.record_eth_revenue(date(2026, 10, 9))
    assert result["baseline"] is True
    assert Decimal(env.revenue[1]["revenue_usd"]) == 0


def test_ada_records_final_epochs_once(env):
    env.history = [
        {"epoch_no": 659, "pool_fees": "900000000"},  # not final at tip 660
        {"epoch_no": 658, "pool_fees": "400000000"},
        {"epoch_no": 657, "pool_fees": "0"},
    ]
    result = recon.record_ada_revenue(TODAY)
    assert result["recorded_epochs"] == 1
    assert env.revenue[0]["period_key"] == "epoch:658"
    assert Decimal(env.revenue[0]["revenue_usd"]) == Decimal(200)  # 400 ADA x $0.5
    assert recon.record_ada_revenue(TODAY)["recorded_epochs"] == 0


def test_overspend_pauses_and_alerts(env):
    env.revenue.append({"asset": "eth", "period_key": "x", "revenue_usd": Decimal(100)})
    # 200 credits x (1 - 0.20) = $160 cost > $100 x 1.05.
    env.granted["eth"] = Decimal(200)
    result = recon.run_delegation_reconciliation_once(TODAY)
    assert result["eth"]["comparison"]["status"] == "overspent"
    assert env.paused == ["eth"] and env.alerts == ["eth"]
    assert result["eth"]["paused"] is True
    assert result["ada"]["paused"] is False


def test_within_tolerance_does_not_pause(env):
    env.revenue.append({"asset": "eth", "period_key": "x", "revenue_usd": Decimal(100)})
    env.granted["eth"] = Decimal(131)  # $104.80 cost <= $105 limit
    recon.run_delegation_reconciliation_once(TODAY)
    assert env.paused == []


def test_grace_covers_launch_lag(env, monkeypatch):
    monkeypatch.setattr(recon.Config, "DELEGATION_RECONCILIATION_GRACE_USD", Decimal("50"))
    env.granted["ada"] = Decimal(50)  # $40 cost, no revenue yet
    recon.run_delegation_reconciliation_once(TODAY)
    assert "ada" not in env.paused


def test_unknown_sums_never_count_as_healthy_or_zero(env, monkeypatch):
    monkeypatch.setattr(recon, "sum_granted_credits", lambda asset: None)
    result = recon.run_delegation_reconciliation_once(TODAY)
    assert result["eth"]["comparison"] == {"status": "unknown"}
    assert env.paused == []


def test_revenue_read_failure_still_compares(env, monkeypatch):
    def boom():
        raise stakewise.VaultReadError("ConnectionError")

    monkeypatch.setattr(stakewise, "read_vault_info", boom)
    env.granted["eth"] = Decimal(10)
    result = recon.run_delegation_reconciliation_once(TODAY)
    assert result["eth"]["revenue"] == {"error": "VaultReadError"}
    assert env.paused == ["eth"]


def test_already_paused_asset_is_not_paused_again(env):
    env.controls = {"eth": {"accruals_paused": True}, "ada": {}}
    env.granted["eth"] = Decimal(10)
    result = recon.run_delegation_reconciliation_once(TODAY)
    assert env.paused == []
    assert result["eth"]["paused"] is True


def test_eth_revenue_records_the_attribution_denominator(env):
    # totalAssets 1000 ETH, fee recipient holds 10 ETH -> 990 ETH staked by others.
    recon.record_eth_revenue(TODAY)
    assert env.revenue[0]["stake_total_native"] == Decimal(990)


def test_ada_revenue_records_the_epoch_active_stake(env):
    env.history = [{"epoch_no": 658, "pool_fees": "400000000", "active_stake": "5000000000000"}]
    recon.record_ada_revenue(TODAY)
    assert env.revenue[0]["stake_total_native"] == Decimal(5_000_000)


@pytest.fixture
def reward_share(env, monkeypatch):
    monkeypatch.setattr(recon.Config, "DELEGATION_PAYOUT_MODEL", "reward_share")
    monkeypatch.setattr(recon.Config, "DELEGATION_REWARD_SHARE", Decimal("0.95"))
    env.discount = {"eth": Decimal(0), "ada": Decimal(0)}
    monkeypatch.setattr(recon, "sum_attributed_discount", lambda asset: env.discount[asset])
    return env


def test_reward_share_within_share_of_revenue_is_ok(reward_share):
    reward_share.revenue.append({"asset": "eth", "period_key": "x", "revenue_usd": Decimal(100)})
    reward_share.discount["eth"] = Decimal(95)
    result = recon.run_delegation_reconciliation_once(TODAY)
    assert result["eth"]["comparison"]["status"] == "ok"
    assert result["eth"]["comparison"]["payout_model"] == "reward_share"
    assert reward_share.paused == []


def test_reward_share_granted_beyond_share_pauses(reward_share):
    reward_share.revenue.append({"asset": "eth", "period_key": "x", "revenue_usd": Decimal(100)})
    # 95 x 1.05 = 99.75 < 100 granted.
    reward_share.discount["eth"] = Decimal(100)
    result = recon.run_delegation_reconciliation_once(TODAY)
    assert result["eth"]["comparison"]["status"] == "overspent"
    assert reward_share.paused == ["eth"] and reward_share.alerts == ["eth"]


def test_reward_share_counts_earlier_v2_credits_too(reward_share):
    reward_share.revenue.append({"asset": "eth", "period_key": "x", "revenue_usd": Decimal(100)})
    reward_share.discount["eth"] = Decimal(50)
    reward_share.granted["eth"] = Decimal(100)  # $80 of v2 credit cost
    result = recon.run_delegation_reconciliation_once(TODAY)
    assert result["eth"]["comparison"]["status"] == "overspent"


def test_reward_share_unknown_discount_sum_is_unknown(reward_share, monkeypatch):
    monkeypatch.setattr(recon, "sum_attributed_discount", lambda asset: None)
    result = recon.run_delegation_reconciliation_once(TODAY)
    assert result["eth"]["comparison"] == {"status": "unknown"}
    assert reward_share.paused == []


def test_discount_liability(monkeypatch):
    monkeypatch.setattr(recon.Config, "DELEGATION_INFERENCE_MARGIN", Decimal("0.20"))
    monkeypatch.setattr(
        recon,
        "list_balances",
        lambda: [
            {"balance_usd": "10", "lifetime_used_usd": "5"},
            {"balance_usd": "0", "lifetime_used_usd": "2.5"},
        ],
    )
    view = recon.discount_liability()
    assert view["discount_used_usd"] == "7.5"
    assert view["outstanding_balance_usd"] == "10"
    assert view["accounts_with_balance"] == 1
    # used x (1 - m) + outstanding at face value.
    assert Decimal(view["cost_usd"]) == Decimal("16")
    monkeypatch.setattr(recon, "list_balances", lambda: None)
    assert recon.discount_liability() == {"status": "unknown"}
