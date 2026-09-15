"""Tests for src/routes/holdings.py -- the holdings-rewards API.

Includes the language guard: this product pays credits for tokens a user
holds in a wallet they proved. We take no custody and promise no return, so
the words that would imply otherwise must not appear anywhere in the route
module, docstrings and endpoint names included.
"""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import src.routes.holdings as holdings_routes
import src.schemas.holdings as holdings_schemas
from src.main import app
from src.security.deps import get_current_user, require_admin_or_env_key, require_superadmin
from src.services.holdings.rewards import HoldingsSnapshotsMissingError

client = TestClient(app)

SUPERADMIN = {"id": 1, "email": "root@example.com", "role": "superadmin"}
ENV_ADMIN = {"role": "admin", "auth": "env_key", "is_admin": True}
USER = {"id": 42, "email": "holder@example.com"}

WALLET = "0x" + "a" * 40
CONTRACT = "0x" + "b" * 40

RATES = [
    {"id": 1, "min_usd": "0", "credits_per_1k_usd_per_day": "1.000000", "is_active": True},
    {"id": 2, "min_usd": "1000", "credits_per_1k_usd_per_day": "2.000000", "is_active": True},
]


@pytest.fixture(autouse=True)
def _isolate_dependency_overrides():
    saved = dict(app.dependency_overrides)
    yield
    app.dependency_overrides.clear()
    app.dependency_overrides.update(saved)


@pytest.fixture
def admin_or_env_override():
    app.dependency_overrides[require_admin_or_env_key] = lambda: ENV_ADMIN


@pytest.fixture
def superadmin_override():
    app.dependency_overrides[require_superadmin] = lambda: SUPERADMIN


@pytest.fixture
def user_override():
    app.dependency_overrides[get_current_user] = lambda: USER


class TestLanguage:
    """This is "holdings rewards". It is never any of the words below --
    each of them would imply custody, a return, or a token we do not
    promise."""

    FORBIDDEN = ("staking", "stake", "yield", "apy", "wayz")

    def test_route_module_avoids_every_forbidden_word(self):
        source = Path(holdings_routes.__file__).read_text().lower()
        offenders = [w for w in self.FORBIDDEN if re.search(rf"\b{w}\w*", source)]
        assert offenders == []

    def test_schema_module_avoids_every_forbidden_word(self):
        source = Path(holdings_schemas.__file__).read_text().lower()
        offenders = [w for w in self.FORBIDDEN if re.search(rf"\b{w}\w*", source)]
        assert offenders == []

    def test_endpoint_paths_and_summaries_avoid_every_forbidden_word(self):
        for route in holdings_routes.router.routes:
            text = f"{route.path} {route.name} {route.endpoint.__doc__ or ''}".lower()
            for word in self.FORBIDDEN:
                assert not re.search(rf"\b{word}\w*", text), (route.path, word)


class TestAuth:
    def test_user_view_requires_a_caller(self):
        assert client.get("/holdings/rewards").status_code in (401, 403)

    def test_reading_rates_requires_admin(self):
        assert client.get("/admin/holdings/rates").status_code in (401, 403)

    def test_replacing_rates_requires_superadmin(self):
        response = client.put(
            "/admin/holdings/rates",
            json={"rates": [{"min_usd": 0, "credits_per_1k_usd_per_day": 1}]},
        )
        assert response.status_code in (401, 403)

    def test_plain_admin_cannot_replace_rates(self, admin_or_env_override):
        response = client.put(
            "/admin/holdings/rates",
            json={"rates": [{"min_usd": 0, "credits_per_1k_usd_per_day": 1}]},
        )
        assert response.status_code in (401, 403)

    def test_registry_writes_require_superadmin(self):
        assert client.get("/admin/holdings/tokens").status_code in (401, 403)
        assert client.post("/admin/holdings/tokens", json={}).status_code in (401, 403)
        assert client.patch("/admin/holdings/tokens/1", json={}).status_code in (401, 403)

    def test_manual_run_requires_superadmin(self):
        assert client.post("/admin/holdings/rewards/run", json={}).status_code in (401, 403)

    def test_summary_requires_admin(self):
        assert client.get("/admin/holdings/rewards/summary").status_code in (401, 403)


class TestUserView:
    def test_returns_value_tier_estimate_and_history(self, user_override):
        accruals = [
            {
                "wallet_address": WALLET,
                "reward_date": "2026-09-14",
                "usd_basis": "1500",
                "credits": "3.000000",
                "status": "paid",
            }
        ]
        with (
            patch(
                "src.routes.holdings.get_wallets_for_user",
                return_value=[{"wallet_address": WALLET}],
            ),
            patch("src.routes.holdings.get_latest_snapshot_usd", return_value=Decimal("2000")),
            patch("src.routes.holdings.get_active_holdings_rates", return_value=RATES),
            patch("src.routes.holdings.list_holdings_accruals_for_wallet", return_value=accruals),
        ):
            response = client.get("/holdings/rewards")

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["total_usd_value"] == "2000"
        wallet = data["wallets"][0]
        assert wallet["address"] == WALLET
        assert wallet["tier_min_usd"] == "1000"
        assert wallet["estimated_credits_per_day"] == "4.000000"
        assert data["totals"]["credits_paid_all"] == "3.000000"
        assert data["history"][0]["reward_date"] == "2026-09-14"

    def test_estimate_is_capped_at_the_daily_ceiling(self, user_override, monkeypatch):
        monkeypatch.setattr(holdings_routes.Config, "HOLDINGS_DAILY_CAP_CREDITS", 2.0)
        with (
            patch(
                "src.routes.holdings.get_wallets_for_user",
                return_value=[{"wallet_address": WALLET}],
            ),
            patch("src.routes.holdings.get_latest_snapshot_usd", return_value=Decimal("100000")),
            patch("src.routes.holdings.get_active_holdings_rates", return_value=RATES),
            patch("src.routes.holdings.list_holdings_accruals_for_wallet", return_value=[]),
        ):
            response = client.get("/holdings/rewards")
        wallet = response.json()["data"]["wallets"][0]
        assert wallet["estimated_credits_per_day"] == "2.000000"
        assert wallet["uncapped_credits_per_day"] == "200.000000"

    def test_never_observed_wallet_reads_as_zero_not_missing(self, user_override):
        with (
            patch(
                "src.routes.holdings.get_wallets_for_user",
                return_value=[{"wallet_address": WALLET}],
            ),
            patch("src.routes.holdings.get_latest_snapshot_usd", return_value=None),
            patch("src.routes.holdings.get_active_holdings_rates", return_value=RATES),
            patch("src.routes.holdings.list_holdings_accruals_for_wallet", return_value=[]),
        ):
            response = client.get("/holdings/rewards")
        wallet = response.json()["data"]["wallets"][0]
        assert wallet["usd_value"] == "0"
        assert wallet["observed"] is False

    def test_account_with_no_wallets_is_an_empty_view_not_an_error(self, user_override):
        with (
            patch("src.routes.holdings.get_wallets_for_user", return_value=[]),
            patch("src.routes.holdings.get_active_holdings_rates", return_value=RATES),
        ):
            response = client.get("/holdings/rewards")
        assert response.status_code == 200
        assert response.json()["data"]["wallets"] == []


class TestRates:
    def test_read_returns_the_active_tiers(self, admin_or_env_override):
        with patch("src.routes.holdings.get_active_holdings_rates", return_value=RATES):
            response = client.get("/admin/holdings/rates")
        assert response.json()["data"]["rates"] == RATES

    def test_replace_writes_and_audits(self, superadmin_override):
        with (
            patch(
                "src.routes.holdings.replace_active_holdings_rates", return_value=RATES
            ) as mock_replace,
            patch("src.routes.holdings.record_audit") as mock_audit,
        ):
            response = client.put(
                "/admin/holdings/rates",
                json={
                    "rates": [
                        {"min_usd": 0, "credits_per_1k_usd_per_day": 1, "note": "base"},
                        {"min_usd": 1000, "credits_per_1k_usd_per_day": 2},
                    ]
                },
            )
        assert response.status_code == 200
        mock_replace.assert_called_once()
        assert mock_audit.call_args.kwargs["action"] == "holdings.rates.update"

    def test_a_set_without_a_zero_floor_is_rejected(self, superadmin_override):
        """Without a zero tier, a wallet below every floor matches nothing
        and is silently skipped instead of earning the base rate."""
        with patch("src.routes.holdings.replace_active_holdings_rates") as mock_replace:
            response = client.put(
                "/admin/holdings/rates",
                json={"rates": [{"min_usd": 1000, "credits_per_1k_usd_per_day": 2}]},
            )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "missing_zero_floor_tier"
        mock_replace.assert_not_called()

    def test_duplicate_floors_are_rejected(self, superadmin_override):
        with patch("src.routes.holdings.replace_active_holdings_rates") as mock_replace:
            response = client.put(
                "/admin/holdings/rates",
                json={
                    "rates": [
                        {"min_usd": 0, "credits_per_1k_usd_per_day": 1},
                        {"min_usd": 0, "credits_per_1k_usd_per_day": 3},
                    ]
                },
            )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "duplicate_rate_floor"
        mock_replace.assert_not_called()

    def test_negative_rate_is_rejected(self, superadmin_override):
        response = client.put(
            "/admin/holdings/rates",
            json={"rates": [{"min_usd": 0, "credits_per_1k_usd_per_day": -1}]},
        )
        assert response.status_code == 422

    def test_empty_set_is_rejected(self, superadmin_override):
        response = client.put("/admin/holdings/rates", json={"rates": []})
        assert response.status_code == 422

    def test_write_failure_is_a_500(self, superadmin_override):
        with patch("src.routes.holdings.replace_active_holdings_rates", return_value=None):
            response = client.put(
                "/admin/holdings/rates",
                json={"rates": [{"min_usd": 0, "credits_per_1k_usd_per_day": 1}]},
            )
        assert response.status_code == 500


class TestRegistry:
    def test_listing_includes_disabled_assets(self, admin_or_env_override):
        rows = [{"id": 1, "symbol": "WETH", "is_enabled": False}]
        with patch("src.routes.holdings.list_all_tokens", return_value=rows):
            response = client.get("/admin/holdings/tokens")
        assert response.json()["data"]["tokens"] == rows

    def test_register_creates_and_audits(self, superadmin_override):
        created = {"id": 7, "symbol": "WETH"}
        with (
            patch("src.routes.holdings.create_token", return_value=created) as mock_create,
            patch("src.routes.holdings.record_audit") as mock_audit,
        ):
            response = client.post(
                "/admin/holdings/tokens",
                json={
                    "chain_id": 1,
                    "contract_address": "0x" + "B" * 40,
                    "symbol": "WETH",
                    "decimals": 18,
                    "price_id": "weth",
                },
            )
        assert response.status_code == 200
        assert mock_create.call_args.kwargs["contract_address"] == CONTRACT
        assert mock_audit.call_args.kwargs["action"] == "holdings.tokens.create"

    def test_native_asset_registers_without_a_contract(self, superadmin_override):
        with (
            patch("src.routes.holdings.create_token", return_value={"id": 8}) as mock_create,
            patch("src.routes.holdings.record_audit"),
        ):
            response = client.post(
                "/admin/holdings/tokens",
                json={
                    "chain_id": 1,
                    "symbol": "ETH",
                    "decimals": 18,
                    "price_id": "ethereum",
                },
            )
        assert response.status_code == 200
        assert mock_create.call_args.kwargs["contract_address"] is None

    def test_malformed_contract_address_is_rejected(self, superadmin_override):
        response = client.post(
            "/admin/holdings/tokens",
            json={
                "chain_id": 1,
                "contract_address": "nope",
                "symbol": "WETH",
                "decimals": 18,
                "price_id": "weth",
            },
        )
        assert response.status_code == 422

    def test_duplicate_registration_is_a_409(self, superadmin_override):
        with patch("src.routes.holdings.create_token", return_value=None):
            response = client.post(
                "/admin/holdings/tokens",
                json={
                    "chain_id": 1,
                    "contract_address": CONTRACT,
                    "symbol": "WETH",
                    "decimals": 18,
                    "price_id": "weth",
                },
            )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "token_already_registered"

    def test_patch_writes_only_the_supplied_fields(self, superadmin_override):
        with (
            patch(
                "src.routes.holdings.update_token", return_value={"id": 7, "is_enabled": False}
            ) as mock_update,
            patch("src.routes.holdings.record_audit") as mock_audit,
        ):
            response = client.patch("/admin/holdings/tokens/7", json={"is_enabled": False})
        assert response.status_code == 200
        assert mock_update.call_args.args == (7, {"is_enabled": False})
        assert mock_audit.call_args.kwargs["action"] == "holdings.tokens.update"

    def test_empty_patch_is_rejected(self, superadmin_override):
        assert client.patch("/admin/holdings/tokens/7", json={}).status_code == 422

    def test_patching_an_unknown_asset_is_a_404(self, superadmin_override):
        with patch("src.routes.holdings.update_token", return_value=None):
            response = client.patch("/admin/holdings/tokens/999", json={"is_enabled": True})
        assert response.status_code == 404


class TestManualRun:
    def test_runs_for_the_given_date_and_audits(self, superadmin_override):
        summary = {"reward_date": "2026-09-14", "paid": 2, "credits_paid": "1.5"}
        with (
            patch(
                "src.routes.holdings.run_holdings_rewards_once", return_value=summary
            ) as mock_run,
            patch("src.routes.holdings.record_audit") as mock_audit,
        ):
            response = client.post(
                "/admin/holdings/rewards/run", json={"reward_date": "2026-09-14"}
            )
        assert response.status_code == 200
        assert response.json()["data"] == summary
        assert str(mock_run.call_args.args[0]) == "2026-09-14"
        assert mock_audit.call_args.kwargs["action"] == "holdings.rewards.run"

    def test_missing_observations_is_a_409(self, superadmin_override):
        with patch(
            "src.routes.holdings.run_holdings_rewards_once",
            side_effect=HoldingsSnapshotsMissingError("no observed balances for 2026-09-14"),
        ):
            response = client.post("/admin/holdings/rewards/run", json={})
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "no_observations_for_date"


class TestSummary:
    def test_reports_totals_and_both_job_runs(self, admin_or_env_override):
        rows = [
            {"reward_date": "2026-09-14", "credits": "3.000000", "status": "paid"},
            {"reward_date": "2026-09-14", "credits": "1.000000", "status": "pending"},
        ]
        runs = {
            "holdings_rewards": {"ok": True, "ran_at": "2026-09-15T00:40:00Z"},
            "holdings_snapshots": {"ok": True, "ran_at": "2026-09-15T00:05:00Z"},
        }
        with (
            patch("src.routes.holdings.list_holdings_accruals_since", return_value=rows),
            patch("src.routes.holdings.get_job_runs", return_value=runs),
        ):
            response = client.get("/admin/holdings/rewards/summary")
        data = response.json()["data"]
        assert data["accruals"] == 2
        assert data["totals"]["credits_paid_all"] == "3.000000"
        assert data["totals"]["pending_credits"] == "1.000000"
        assert data["last_run"]["ok"] is True
        assert data["last_snapshot_run"]["ran_at"] == "2026-09-15T00:05:00Z"
