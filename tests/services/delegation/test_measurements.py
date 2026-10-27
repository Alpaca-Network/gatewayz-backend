"""Tests for src.services.delegation.measurements -- the sweep that records
each linked wallet's delegated amount.

The rules: a measured zero IS recorded; anything we could not measure (RPC
failure, Koios failure, missing price) records nothing; Cardano stake only
counts from its active epoch; unconfigured assets make no external call.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

import src.services.delegation.measurements as measurements
from src.services.delegation import koios, stakewise

NOW = datetime(2026, 10, 8, 6, 10, tzinfo=UTC)
EVM1 = "0x" + "1" * 40
EVM2 = "0x" + "2" * 40
ADA1 = "stake1uyehkck0lajq8gr28t9uxnuvgcqrc6070x3k9r8048z8y5gh6ffgw"
ADA2 = "stake1u9" + "q" * 51


@pytest.fixture
def env(monkeypatch):
    recorded: list[tuple] = []
    state = SimpleNamespace(
        recorded=recorded,
        wallets=[
            {"wallet_address": EVM1, "chain_namespace": "eip155"},
            {"wallet_address": EVM2},  # pre-column row: EVM
            {"wallet_address": ADA1, "chain_namespace": "cip34"},
            {"wallet_address": ADA2, "chain_namespace": "cip34"},
        ],
        shares={EVM1: 2 * 10**18, EVM2: 0},
        prices={"ethereum": Decimal(2000), "cardano": Decimal("0.5")},
        delegators=[
            {"stake_address": ADA1, "amount": "1000000000", "active_epoch_no": 600},
            {"stake_address": ADA2, "amount": "5000000", "active_epoch_no": 661},
        ],
        koios_fails=False,
        calls=[],
    )
    monkeypatch.setattr(measurements.Config, "DELEGATED_STAKING_ENABLED", True)
    monkeypatch.setattr(measurements.Config, "STAKEWISE_VAULT_ADDRESS", "0x" + "c" * 40)
    monkeypatch.setattr(measurements.Config, "CARDANO_POOL_ID", "pool1" + "x" * 51)
    monkeypatch.setattr(measurements, "list_all_wallets", lambda: list(state.wallets))
    monkeypatch.setattr(
        measurements,
        "record_measurement",
        lambda w, a, amt, usd, t: recorded.append((w, a, amt, usd)) or True,
    )

    def prices(ids):
        return {
            i: SimpleNamespace(price=state.prices[i])
            for i in ids
            if state.prices.get(i) is not None
        }

    monkeypatch.setattr(measurements, "get_usd_prices", prices)

    def read_shares(address):
        state.calls.append(("shares", address))
        value = state.shares[address]
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(stakewise, "read_shares", read_shares)
    monkeypatch.setattr(stakewise, "convert_to_assets", lambda s: s * 105 // 100)

    def delegators(pool):
        state.calls.append(("koios", pool))
        if state.koios_fails:
            raise koios.KoiosError("Koios pool_delegators failed: HTTPError HTTP 503")
        return list(state.delegators)

    monkeypatch.setattr(koios, "list_pool_delegators", delegators)
    monkeypatch.setattr(koios, "get_tip_epoch", lambda: 660)
    return state


def test_disabled_makes_no_call(env, monkeypatch):
    monkeypatch.setattr(measurements.Config, "DELEGATED_STAKING_ENABLED", False)
    assert measurements.run_delegation_measurements_once(NOW) == {"skipped": "disabled"}
    assert env.calls == [] and env.recorded == []


def test_unconfigured_assets_make_no_external_call(env, monkeypatch):
    monkeypatch.setattr(measurements.Config, "STAKEWISE_VAULT_ADDRESS", None)
    monkeypatch.setattr(measurements.Config, "CARDANO_POOL_ID", None)
    result = measurements.run_delegation_measurements_once(NOW)
    assert result["eth"] == {"skipped": "unconfigured"}
    assert result["ada"] == {"skipped": "unconfigured"}
    assert env.calls == []


def test_eth_records_assets_and_a_measured_zero(env):
    result = measurements.run_delegation_measurements_once(NOW)
    eth = [r for r in env.recorded if r[1] == "eth"]
    assert (EVM1, "eth", 21 * 10**17, Decimal("4200")) in eth
    assert (EVM2, "eth", 0, Decimal(0)) in eth
    assert result["eth"]["recorded"] == 2 and result["eth"]["zero"] == 1
    # Cardano wallets are never read as EVM addresses.
    assert all(c[1] != ADA1 for c in env.calls if c[0] == "shares")


def test_eth_read_failure_records_nothing_for_that_wallet(env):
    env.shares[EVM1] = stakewise.VaultReadError("ConnectionError")
    result = measurements.run_delegation_measurements_once(NOW)
    eth = [r for r in env.recorded if r[1] == "eth"]
    assert [r[0] for r in eth] == [EVM2]
    assert result["eth"]["read_failed"] == 1


def test_missing_price_records_nothing(env):
    env.prices["ethereum"] = None
    result = measurements.run_delegation_measurements_once(NOW)
    assert result["eth"]["skipped"] == "no_price"
    assert not [r for r in env.recorded if r[1] == "eth"]


def test_ada_counts_only_active_delegations_and_records_zero_otherwise(env):
    result = measurements.run_delegation_measurements_once(NOW)
    ada = {r[0]: r for r in env.recorded if r[1] == "ada"}
    assert ada[ADA1][2] == 1_000_000_000 and ada[ADA1][3] == Decimal(500)
    # ADA2 delegated, but its stake is not active until epoch 661.
    assert ada[ADA2][2] == 0 and ada[ADA2][3] == Decimal(0)
    assert result["ada"]["not_yet_active"] == 1


def test_ada_wallet_not_delegated_is_a_zero(env):
    env.delegators = []
    measurements.run_delegation_measurements_once(NOW)
    ada = [r for r in env.recorded if r[1] == "ada"]
    assert {r[2] for r in ada} == {0}
    assert len(ada) == 2


def test_koios_failure_records_nothing(env):
    env.koios_fails = True
    result = measurements.run_delegation_measurements_once(NOW)
    assert result["ada"]["skipped"] == "koios_unavailable"
    assert not [r for r in env.recorded if r[1] == "ada"]


def test_no_cardano_wallets_skips_koios(env):
    env.wallets = [w for w in env.wallets if w.get("chain_namespace") != "cip34"]
    measurements.run_delegation_measurements_once(NOW)
    assert not [c for c in env.calls if c[0] == "koios"]
