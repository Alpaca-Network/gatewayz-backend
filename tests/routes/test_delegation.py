"""Tests for src/routes/delegation.py -- the delegated-staking API.

Includes the legal guardrail: the allowance is admin-set and changeable, so
the API carries the disclaimer and never calls the rate a fixed or
guaranteed return.
"""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import src.routes.delegation as delegation_routes
from src.main import app
from src.security.deps import get_current_user, require_admin_or_env_key, require_superadmin

client = TestClient(app)

SUPERADMIN = {"id": 1, "email": "root@example.com", "role": "superadmin"}
ENV_ADMIN = {"role": "admin", "auth": "env_key", "is_admin": True}
USER = {"id": 42, "email": "staker@example.com"}
WALLET = "0x" + "a" * 40
VAULT = "0x" + "c" * 40


@pytest.fixture(autouse=True)
def _isolate():
    saved = dict(app.dependency_overrides)
    yield
    app.dependency_overrides.clear()
    app.dependency_overrides.update(saved)


@pytest.fixture
def superadmin():
    app.dependency_overrides[require_superadmin] = lambda: SUPERADMIN


@pytest.fixture
def env_admin():
    app.dependency_overrides[require_admin_or_env_key] = lambda: ENV_ADMIN


@pytest.fixture
def user():
    app.dependency_overrides[get_current_user] = lambda: USER


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(delegation_routes.Config, "DELEGATED_STAKING_ENABLED", True)
    monkeypatch.setattr(delegation_routes.Config, "STAKEWISE_VAULT_ADDRESS", VAULT)
    monkeypatch.setattr(delegation_routes.Config, "CARDANO_POOL_ID", None)


class TestGuardrail:
    def test_module_never_promises_a_return(self):
        source = Path(delegation_routes.__file__).read_text().lower()
        for phrase in (
            "guaranteed yield",
            "fixed yield",
            "guaranteed apy",
            "risk-free",
            "risk free",
        ):
            assert phrase not in source
        assert not re.search(r"\bapy\b", source)

    def test_disclaimer_text(self):
        assert delegation_routes.DISCLAIMER == (
            "Rates are set by Gatewayz, can change at any time, and are not a guaranteed return."
        )


class TestStatus:
    def test_dark_by_default(self, monkeypatch):
        monkeypatch.setattr(delegation_routes.Config, "DELEGATED_STAKING_ENABLED", False)
        monkeypatch.setattr(delegation_routes.Config, "STAKEWISE_VAULT_ADDRESS", None)
        monkeypatch.setattr(delegation_routes.Config, "CARDANO_POOL_ID", None)
        monkeypatch.setattr(delegation_routes.Config, "STAKEWISE_VAULT_CHAIN_ID", 1)
        response = client.get("/delegation/status")
        assert response.status_code == 200
        assert response.json() == {
            "success": True,
            "data": {
                "enabled": False,
                "eth": {"vault_address": None, "chain_id": 1, "fee_percent": None},
                "cardano": {"pool_id": None},
                "allowance_rates": [],
                "disclaimer": delegation_routes.DISCLAIMER,
            },
        }

    def test_enabled_shows_vault_fee_and_active_configured_rates(self, enabled):
        rates = {
            "eth": {"asset": "eth", "credits_per_1k_usd_per_day": "0.100000"},
            "ada": {"asset": "ada", "credits_per_1k_usd_per_day": "0.200000"},
        }
        with (
            patch.object(delegation_routes, "get_active_rates", return_value=rates),
            patch.object(delegation_routes.stakewise, "cached_fee_percent_bps", return_value=9900),
        ):
            data = client.get("/delegation/status").json()["data"]
        # EIP-55 checksummed.
        assert data["eth"]["vault_address"].lower() == VAULT
        assert data["eth"]["vault_address"] != VAULT
        assert data["eth"]["fee_percent"] == "99.00"
        # ADA's pool is not configured, so its rate is not published.
        assert data["allowance_rates"] == [
            {"asset": "eth", "credits_per_1k_usd_per_day": "0.100000"}
        ]


class TestAuth:
    def test_rewards_requires_a_caller(self):
        assert client.get("/delegation/rewards").status_code in (401, 403)

    def test_admin_reads_require_admin(self):
        assert client.get("/admin/delegation/rates").status_code in (401, 403)
        assert client.get("/admin/delegation/reconciliation").status_code in (401, 403)

    def test_mutations_require_superadmin(self, env_admin):
        body = {"rates": [{"asset": "eth", "credits_per_1k_usd_per_day": 1}]}
        assert client.put("/admin/delegation/rates", json=body).status_code in (401, 403)
        assert client.post("/admin/delegation/run", json={"job": "measure"}).status_code in (
            401,
            403,
        )
        assert client.post("/admin/delegation/resume", json={"asset": "eth"}).status_code in (
            401,
            403,
        )


class TestRewards:
    def test_positions_allowance_totals_history(self, user, enabled):
        measurement = {
            "asset": "eth",
            "amount_raw": str(2 * 10**18),
            "usd_value": "4000",
            "taken_at": "2026-10-08T06:10:00+00:00",
        }
        accruals = [
            {
                "reward_date": "2099-01-01",
                "asset": "eth",
                "wallet_address": WALLET,
                "usd_basis": "4000",
                "credits": "0.4",
                "status": "paid",
            },
            {
                "reward_date": "2099-01-02",
                "asset": "eth",
                "wallet_address": WALLET,
                "usd_basis": "4000",
                "credits": "0.4",
                "status": "pending",
            },
        ]
        rates = {"eth": {"asset": "eth", "credits_per_1k_usd_per_day": "0.1"}}
        with (
            patch.object(
                delegation_routes, "get_wallets_for_user", return_value=[{"wallet_address": WALLET}]
            ),
            patch.object(delegation_routes, "get_latest_measurements", return_value=[measurement]),
            patch.object(delegation_routes, "get_active_rates", return_value=rates),
            patch.object(delegation_routes, "list_accruals_for_wallet", return_value=accruals),
        ):
            data = client.get("/delegation/rewards").json()["data"]
        assert data["linked_wallets"] == [{"asset": "eth", "wallet_address": WALLET}]
        assert data["positions"] == [
            {
                "asset": "eth",
                "wallet_address": WALLET,
                "amount": "2.000000000000000000",
                "usd_value": "4000",
                "measured_at": "2026-10-08T06:10:00+00:00",
            }
        ]
        assert data["allowance"]["credits_per_day_estimate"] == "0.400000"
        assert data["allowance"]["month_estimate_usd"] == "12.000000"
        assert data["totals"] == {"pending": "0.4", "paid": "0.4"}
        assert [h["reward_date"] for h in data["history"]] == ["2099-01-02", "2099-01-01"]
        assert data["disclaimer"] == delegation_routes.DISCLAIMER


class TestAdmin:
    def test_put_rates_refuses_an_unconfigured_asset(self, superadmin, enabled):
        with patch.object(delegation_routes, "replace_active_rate") as replace:
            response = client.put(
                "/admin/delegation/rates",
                json={"rates": [{"asset": "ada", "credits_per_1k_usd_per_day": "0.2"}]},
            )
        assert response.status_code == 422
        replace.assert_not_called()

    def test_put_rates_replaces_and_audits(self, superadmin, enabled):
        with (
            patch.object(delegation_routes, "replace_active_rate", return_value={"id": 3}) as rep,
            patch.object(delegation_routes, "deactivate_rate", return_value=True) as deact,
            patch.object(delegation_routes, "get_active_rates", return_value={}),
            patch.object(delegation_routes, "record_audit") as audit,
        ):
            response = client.put(
                "/admin/delegation/rates",
                json={
                    "rates": [
                        {"asset": "eth", "credits_per_1k_usd_per_day": "0.1", "note": "launch"},
                        {"asset": "ada", "credits_per_1k_usd_per_day": "0", "is_active": False},
                    ]
                },
            )
        assert response.status_code == 200
        rep.assert_called_once_with("eth", Decimal("0.1"), "launch")
        deact.assert_called_once_with("ada")
        assert audit.call_args.kwargs["action"] == "delegation.rates.update"

    def test_put_rates_rejects_duplicates_and_negatives(self, superadmin):
        dup = {"rates": [{"asset": "eth", "credits_per_1k_usd_per_day": 1}] * 2}
        neg = {"rates": [{"asset": "eth", "credits_per_1k_usd_per_day": -1}]}
        assert client.put("/admin/delegation/rates", json=dup).status_code == 422
        assert client.put("/admin/delegation/rates", json=neg).status_code == 422

    def test_get_rates_shows_suggestion(self, env_admin, monkeypatch):
        monkeypatch.setattr(
            delegation_routes.Config,
            "DELEGATION_EXPECTED_DAILY_REVENUE_PER_USD_ETH",
            Decimal("0.00008"),
        )
        monkeypatch.setattr(delegation_routes.Config, "DELEGATION_INFERENCE_MARGIN", Decimal("0.2"))
        with patch.object(delegation_routes, "get_active_rates", return_value={}):
            data = client.get("/admin/delegation/rates").json()["data"]
        assert data["suggested"]["eth"] == "0.100000"
        assert data["rates"][0]["is_active"] is False

    @pytest.mark.parametrize(
        "job,target",
        [
            ("measure", "run_delegation_measurements_once"),
            ("accrue", "run_delegation_accruals_once"),
            ("reconcile", "run_delegation_reconciliation_once"),
        ],
    )
    def test_run_dispatches_and_audits(self, superadmin, job, target):
        with (
            patch.object(delegation_routes, target, return_value={"ok": True}) as fn,
            patch.object(delegation_routes, "record_audit") as audit,
        ):
            response = client.post("/admin/delegation/run", json={"job": job})
        assert response.status_code == 200
        fn.assert_called_once()
        assert audit.call_args.kwargs["action"] == f"delegation.run.{job}"

    def test_resume_unpauses_and_audits(self, superadmin):
        with (
            patch.object(delegation_routes, "resume_accruals", return_value={"asset": "eth"}) as r,
            patch.object(delegation_routes, "record_audit") as audit,
        ):
            response = client.post("/admin/delegation/resume", json={"asset": "eth"})
        assert response.status_code == 200
        assert response.json()["data"] == {"asset": "eth", "paused": False}
        r.assert_called_once_with("eth", "root@example.com")
        assert audit.call_args.kwargs["action"] == "delegation.resume"

    def test_reconciliation_view(self, env_admin):
        view = {"enabled": False, "eth": {"status": "ok"}, "ada": {"status": "ok"}}
        with (
            patch.object(delegation_routes, "reconciliation_view", return_value=dict(view)),
            patch.object(delegation_routes, "list_recent_revenue", return_value=[]),
        ):
            data = client.get("/admin/delegation/reconciliation").json()["data"]
        assert data["eth"] == {"status": "ok"} and data["recent_revenue"] == []
