"""Tests for src/routes/admin_emission.py (Chutes-style WAYZ emission
rewards admin API, gatewayz-backend tokenomics)."""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.main import app
from src.security.deps import require_admin_or_env_key, require_superadmin

client = TestClient(app)

SUPERADMIN = {"id": 1, "email": "root@example.com", "role": "superadmin"}
ENV_ADMIN = {"role": "admin", "auth": "env_key", "is_admin": True}


@pytest.fixture(autouse=True)
def _isolate_dependency_overrides():
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
    def test_get_epochs_requires_admin(self):
        response = client.get("/admin/emission/epochs")
        assert response.status_code in (401, 403)

    def test_get_epoch_scores_requires_admin(self):
        response = client.get("/admin/emission/epochs/2026-09-12")
        assert response.status_code in (401, 403)

    def test_run_requires_superadmin(self):
        response = client.post("/admin/emission/run", json={})
        assert response.status_code in (401, 403)

    def test_get_config_requires_admin(self):
        response = client.get("/admin/emission/config")
        assert response.status_code in (401, 403)

    def test_put_config_requires_superadmin(self):
        response = client.put("/admin/emission/config")
        assert response.status_code in (401, 403)

    def test_plain_admin_cannot_run_epoch(self, admin_or_env_override):
        """require_superadmin, not require_admin_or_env_key -- an admin
        override alone must not satisfy this route."""
        response = client.post("/admin/emission/run", json={})
        assert response.status_code in (401, 403)


class TestGetEpochs:
    @patch("src.routes.admin_emission.list_epochs")
    def test_returns_recent_epochs(self, mock_list, admin_or_env_override):
        mock_list.return_value = [{"epoch_date": "2026-09-12", "status": "allocated"}]
        response = client.get("/admin/emission/epochs")
        assert response.status_code == 200
        assert response.json()["data"]["epochs"][0]["epoch_date"] == "2026-09-12"
        mock_list.assert_called_once_with(limit=30)

    @patch("src.routes.admin_emission.list_epochs")
    def test_limit_is_bounded(self, mock_list, admin_or_env_override):
        mock_list.return_value = []
        client.get("/admin/emission/epochs", params={"limit": 9999})
        mock_list.assert_called_once_with(limit=90)


class TestGetEpochScores:
    @patch("src.routes.admin_emission.list_provider_scores_for_epoch")
    def test_returns_scores_for_date(self, mock_scores, admin_or_env_override):
        mock_scores.return_value = [{"provider_id": 1, "share": "0.5"}]
        response = client.get("/admin/emission/epochs/2026-09-12")
        assert response.status_code == 200
        body = response.json()["data"]
        assert body["epoch_date"] == "2026-09-12"
        assert body["scores"][0]["provider_id"] == 1
        mock_scores.assert_called_once_with("2026-09-12")


_YESTERDAY = (datetime.now(UTC).date() - timedelta(days=1)).isoformat()


class TestRunEmissionEpoch:
    @patch("src.routes.admin_emission.record_audit")
    @patch("src.routes.admin_emission.run_emission_epoch")
    def test_runs_and_audits(self, mock_run, mock_audit, superadmin_override):
        mock_run.return_value = {"epoch_date": "2026-09-12", "providers_scored": 3}
        response = client.post("/admin/emission/run", json={"epoch_date": _YESTERDAY})
        assert response.status_code == 200
        assert response.json()["data"]["epoch_date"] == "2026-09-12"  # what the job returned
        mock_audit.assert_called_once()
        assert mock_audit.call_args.kwargs["action"] == "emission.epoch.run"

    @patch("src.routes.admin_emission.record_audit")
    @patch("src.routes.admin_emission.run_emission_epoch")
    def test_returns_the_two_way_summary_unchanged(self, mock_run, mock_audit, superadmin_override):
        summary = {
            "epoch_date": "2026-09-12",
            "emission_wei": "100000000000000000000000",
            "split": {"providers_wei": "0", "treasury_wei": "100000000000000000000000"},
            "providers_scored": 3,
        }
        mock_run.return_value = summary
        response = client.post("/admin/emission/run", json={"epoch_date": _YESTERDAY})
        assert response.status_code == 200
        assert response.json() == {"success": True, "data": summary}
        assert "staker" not in response.text

    def test_run_route_has_no_stale_stake_sync_guard(self):
        """The 409 stake_sync_stale path existed only for the staker split."""
        import src.routes.admin_emission as admin_emission

        assert not hasattr(admin_emission, "StakingRewardsStaleError")
        assert not hasattr(admin_emission, "_conflict")


class TestConfig:
    def test_get_config_reports_disabled_reason(self, admin_or_env_override):
        with patch("src.routes.admin_emission.is_emission_job_disabled", return_value="bad sum"):
            response = client.get("/admin/emission/config")
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["disabled_reason"] == "bad sum"
        assert "rewards_mode" in data
        assert "split_bps" in data

    def test_get_config_is_a_two_way_split_with_no_staker_fields(self, admin_or_env_override):
        response = client.get("/admin/emission/config")
        assert response.status_code == 200
        data = response.json()["data"]
        assert set(data) == {
            "rewards_mode",
            "daily_emission_wayz",
            "split_bps",
            "score_weights_bps",
            "exponent_above_median",
            "provider_pool_usd_per_day",
            "provider_payout_asset",
            "cron_utc",
            "disabled_reason",
        }
        assert set(data["split_bps"]) == {"providers", "treasury"}
        assert "staker" not in response.text

    def test_put_config_returns_501(self, superadmin_override):
        with patch("src.routes.admin_emission.record_audit") as mock_audit:
            response = client.put("/admin/emission/config")
        assert response.status_code == 501
        mock_audit.assert_called_once()


class TestManualRunDates:
    """A manual run covers a finished UTC day from the last week only."""

    @pytest.mark.parametrize(
        "days_ago, detail",
        [
            (0, "epoch_date_not_finished"),
            (-1, "epoch_date_not_finished"),
            (8, "epoch_date_too_old"),
        ],
    )
    @patch("src.routes.admin_emission.run_emission_epoch")
    def test_dates_outside_the_window_are_refused_before_the_job(
        self, mock_run, superadmin_override, days_ago, detail
    ):
        day = (datetime.now(UTC).date() - timedelta(days=days_ago)).isoformat()
        response = client.post("/admin/emission/run", json={"epoch_date": day})
        assert response.status_code == 400
        assert response.json()["error"]["context"]["parameter_value"] == detail
        mock_run.assert_not_called()

    @pytest.mark.parametrize("days_ago", [1, 7])
    @patch("src.routes.admin_emission.record_audit")
    @patch("src.routes.admin_emission.run_emission_epoch", return_value={"epoch_date": "x"})
    def test_finished_days_in_the_last_week_run(
        self, mock_run, mock_audit, superadmin_override, days_ago
    ):
        day = (datetime.now(UTC).date() - timedelta(days=days_ago)).isoformat()
        response = client.post("/admin/emission/run", json={"epoch_date": day})
        assert response.status_code == 200
        mock_run.assert_called_once()

    @patch("src.routes.admin_emission.record_audit")
    @patch("src.routes.admin_emission.run_emission_epoch", return_value={"epoch_date": "x"})
    def test_no_date_means_yesterday_and_is_allowed(
        self, mock_run, mock_audit, superadmin_override
    ):
        assert client.post("/admin/emission/run", json={}).status_code == 200
