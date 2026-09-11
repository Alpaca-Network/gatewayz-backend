"""Tests for src/routes/admin_staking.py (staking-rewards admin API)."""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.main import app
from src.security.deps import require_admin_or_env_key, require_superadmin
from src.services.staking_rewards import StakingRewardsStaleError

client = TestClient(app)

SUPERADMIN = {"id": 1, "email": "root@example.com", "role": "superadmin"}
ENV_ADMIN = {"role": "admin", "auth": "env_key", "is_admin": True}


@pytest.fixture(autouse=True)
def _isolate_dependency_overrides():
    """Snapshot and restore the FULL app.dependency_overrides dict around
    every test in this module -- same convention as
    tests/routes/test_admin_staff.py."""
    saved = dict(app.dependency_overrides)
    yield
    app.dependency_overrides.clear()
    app.dependency_overrides.update(saved)


@pytest.fixture
def admin_or_env_override():
    def _set(actor=None):
        app.dependency_overrides[require_admin_or_env_key] = lambda: actor or ENV_ADMIN

    _set()
    return _set


@pytest.fixture
def superadmin_override():
    def _set(actor=None):
        app.dependency_overrides[require_superadmin] = lambda: actor or SUPERADMIN

    _set()
    return _set


class TestAuth:
    def test_get_reward_rates_requires_admin(self):
        response = client.get("/admin/staking/reward-rates")
        assert response.status_code in (401, 403)

    def test_put_reward_rates_requires_superadmin(self):
        response = client.put(
            "/admin/staking/reward-rates",
            json={"rates": [{"min_stake_wayz": 0, "credits_per_1k_wayz_per_day": 0.01}]},
        )
        assert response.status_code in (401, 403)

    def test_run_rewards_requires_superadmin(self):
        response = client.post("/admin/staking/rewards/run", json={})
        assert response.status_code in (401, 403)

    def test_get_summary_requires_admin(self):
        response = client.get("/admin/staking/rewards/summary")
        assert response.status_code in (401, 403)

    def test_plain_admin_cannot_put_reward_rates(self):
        """require_superadmin, not require_admin_or_env_key -- an admin
        override alone must not satisfy this route."""
        app.dependency_overrides[require_admin_or_env_key] = lambda: ENV_ADMIN
        response = client.put(
            "/admin/staking/reward-rates",
            json={"rates": [{"min_stake_wayz": 0, "credits_per_1k_wayz_per_day": 0.01}]},
        )
        assert response.status_code in (401, 403)


class TestGetRewardRates:
    def test_returns_active_rates(self, admin_or_env_override):
        rows = [{"id": 1, "min_stake_wayz": "0", "credits_per_1k_wayz_per_day": "0.010000"}]
        with patch("src.routes.admin_staking.get_active_rates", return_value=rows):
            response = client.get("/admin/staking/reward-rates")
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["data"]["rates"] == rows


class TestUpdateRewardRates:
    def test_replaces_rates_and_audits(self, superadmin_override):
        new_rows = [{"id": 5, "min_stake_wayz": 0, "credits_per_1k_wayz_per_day": "0.020000"}]
        with (
            patch(
                "src.routes.admin_staking.replace_active_rates", return_value=new_rows
            ) as mock_replace,
            patch("src.routes.admin_staking.record_audit") as mock_audit,
        ):
            response = client.put(
                "/admin/staking/reward-rates",
                json={
                    "rates": [
                        {
                            "min_stake_wayz": 0,
                            "credits_per_1k_wayz_per_day": 0.02,
                            "note": "updated",
                        }
                    ]
                },
            )

        assert response.status_code == 200
        body = response.json()
        assert body["data"]["rates"] == new_rows
        mock_replace.assert_called_once()
        mock_audit.assert_called_once()
        args, kwargs = mock_audit.call_args
        assert kwargs["action"] == "staking.rates.update"

    def test_empty_rates_list_is_rejected(self, superadmin_override):
        response = client.put("/admin/staking/reward-rates", json={"rates": []})
        assert response.status_code == 422

    def test_db_failure_returns_500(self, superadmin_override):
        with patch("src.routes.admin_staking.replace_active_rates", return_value=None):
            response = client.put(
                "/admin/staking/reward-rates",
                json={"rates": [{"min_stake_wayz": 0, "credits_per_1k_wayz_per_day": 0.01}]},
            )
        assert response.status_code == 500


class TestRunRewardsNow:
    def test_runs_and_audits_with_summary(self, superadmin_override):
        summary = {
            "reward_date": "2026-09-10",
            "wallets": 2,
            "paid": 1,
            "pending": 1,
            "skipped": 0,
            "credits_paid": "0.05",
            "capped": 0,
            "duration": 0.01,
        }
        with (
            patch(
                "src.routes.admin_staking.run_staking_rewards_once", return_value=summary
            ) as mock_run,
            patch("src.routes.admin_staking.record_audit") as mock_audit,
        ):
            response = client.post("/admin/staking/rewards/run", json={"reward_date": "2026-09-10"})

        assert response.status_code == 200
        body = response.json()
        assert body["data"] == summary
        mock_run.assert_called_once()
        mock_audit.assert_called_once()
        assert mock_audit.call_args.kwargs["action"] == "staking.rewards.run"

    def test_stale_sync_returns_409(self, superadmin_override):
        with patch(
            "src.routes.admin_staking.run_staking_rewards_once",
            side_effect=StakingRewardsStaleError("stake_sync_stale"),
        ):
            response = client.post("/admin/staking/rewards/run", json={})

        assert response.status_code == 409
        body = response.json()
        assert body["error"]["code"] == "stake_sync_stale"
        assert body["error"]["context"]["parameter_value"] == "stake_sync_stale"

    def test_no_reward_date_passes_none(self, superadmin_override):
        with (
            patch(
                "src.routes.admin_staking.run_staking_rewards_once",
                return_value={"reward_date": "x"},
            ) as mock_run,
            patch("src.routes.admin_staking.record_audit"),
        ):
            client.post("/admin/staking/rewards/run", json={})
        mock_run.assert_called_once_with(None)


class TestRewardsSummary:
    def test_returns_last_run_and_totals(self, admin_or_env_override):
        last_run = {"name": "staking_rewards", "ok": True, "ran_at": "2026-09-10T00:20:00+00:00"}
        totals = {
            "totals": {"credits_paid_30d": "1", "credits_paid_all": "2", "pending_credits": "0"}
        }
        with (
            patch(
                "src.routes.admin_staking.get_job_runs", return_value={"staking_rewards": last_run}
            ),
            patch("src.routes.admin_staking.get_global_rewards_summary", return_value=totals),
        ):
            response = client.get("/admin/staking/rewards/summary")

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["last_run"] == last_run
        assert data["totals"] == totals["totals"]
