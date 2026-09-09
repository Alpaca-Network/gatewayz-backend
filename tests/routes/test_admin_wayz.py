"""Tests for GET /admin/wayz/status (src/routes/admin_wayz.py)."""

from unittest.mock import patch

from fastapi.testclient import TestClient

from src.main import app
from src.security.deps import require_admin_or_env_key

client = TestClient(app)


def _override_admin():
    app.dependency_overrides[require_admin_or_env_key] = lambda: {
        "role": "admin",
        "auth": "env_key",
        "is_admin": True,
    }


def _clear_override():
    app.dependency_overrides.pop(require_admin_or_env_key, None)


class TestAuth:
    def test_401_or_403_without_credentials(self):
        response = client.get("/admin/wayz/status")
        assert response.status_code in (401, 403)

    def test_200_with_env_admin_key(self, monkeypatch):
        monkeypatch.setenv("ADMIN_API_KEY", "test-admin-key-for-wayz-status")
        response = client.get(
            "/admin/wayz/status",
            headers={"Authorization": "Bearer test-admin-key-for-wayz-status"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert "data" in body


class TestResponseShape:
    def setup_method(self, _method):
        _override_admin()

    def teardown_method(self, _method):
        _clear_override()

    def test_envelope_and_top_level_keys(self):
        response = client.get("/admin/wayz/status")
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        data = body["data"]
        for key in (
            "generated_at",
            "config",
            "jobs",
            "staking",
            "faucet",
            "wallets",
            "gpu",
            "pending_approvals",
        ):
            assert key in data

    def test_config_block_has_expected_fields(self):
        response = client.get("/admin/wayz/status")
        config = response.json()["data"]["config"]
        for key in (
            "chain_id",
            "token_address",
            "staking_address",
            "deploy_block",
            "faucet_configured",
            "rewards_pool_configured",
            "privy_verification_mode",
            "community_routing_enabled",
            "upstream_pseudonym_enabled",
            "spotcheck_reference_provider",
        ):
            assert key in config

    def test_jobs_block_lists_every_wrapped_job(self):
        response = client.get("/admin/wayz/status")
        jobs = response.json()["data"]["jobs"]
        for name in (
            "model_sync",
            "price_refresh",
            "ledger_reconciliation",
            "retention_cleanup",
            "wayz_staking_sync",
            "gpu_spot_check",
            "gpu_settlement",
            "pricing_drift",
            "gpu_liveness_sweep",
            "gpu_rollup",
        ):
            assert name in jobs
            assert "interval_minutes" in jobs[name]
            assert "stale" in jobs[name]

    def test_job_that_never_ran_is_stale_with_null_fields(self):
        with patch("src.routes.admin_wayz.get_job_runs", return_value={}):
            response = client.get("/admin/wayz/status")
        jobs = response.json()["data"]["jobs"]
        assert jobs["model_sync"]["ok"] is None
        assert jobs["model_sync"]["ran_at"] is None
        assert jobs["model_sync"]["stale"] is True

    def test_stale_job_flagged_when_last_run_beyond_2x_interval(self):
        import json as _json
        from datetime import UTC, datetime, timedelta

        stale_ran_at = (datetime.now(UTC) - timedelta(days=1)).isoformat()
        fake_record = {
            "name": "gpu_liveness_sweep",
            "ok": True,
            "ran_at": stale_ran_at,
            "duration_ms": 10,
            "summary": {},
            "error": None,
        }
        with patch(
            "src.routes.admin_wayz.get_job_runs",
            return_value={"gpu_liveness_sweep": fake_record},
        ):
            response = client.get("/admin/wayz/status")
        job = response.json()["data"]["jobs"]["gpu_liveness_sweep"]
        assert job["stale"] is True
        assert job["ok"] is True  # last known outcome preserved even though stale
        del _json  # unused, kept for readability of the timestamp construction above

    def test_amounts_are_decimal_strings(self):
        response = client.get("/admin/wayz/status")
        data = response.json()["data"]
        staking = data["staking"]
        if "error" not in staking:
            assert isinstance(staking["total_staked_wei"], str)
        gpu = data["gpu"]
        if "error" not in gpu:
            for key in ("accrued_wei", "settling_wei", "settled_wei", "void_wei"):
                assert isinstance(gpu["earnings"][key], str)


class TestSubBlockDegradation:
    def setup_method(self, _method):
        _override_admin()

    def teardown_method(self, _method):
        _clear_override()

    def test_staking_block_degrades_independently(self):
        with patch(
            "src.routes.admin_wayz.get_stake_totals",
            side_effect=RuntimeError("db exploded"),
        ):
            response = client.get("/admin/wayz/status")
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["staking"] == {"error": "RuntimeError"}
        # Every other block is still populated, not blanked out.
        assert "error" not in data["config"]

    def test_faucet_block_degrades_independently(self):
        with patch(
            "src.routes.admin_wayz.get_claim_stats",
            side_effect=ValueError("bad data"),
        ):
            response = client.get("/admin/wayz/status")
        assert response.status_code == 200
        assert response.json()["data"]["faucet"] == {"error": "ValueError"}

    def test_gpu_block_degrades_independently(self):
        with patch(
            "src.routes.admin_wayz.count_providers_by_status",
            side_effect=RuntimeError("boom"),
        ):
            response = client.get("/admin/wayz/status")
        assert response.status_code == 200
        assert response.json()["data"]["gpu"] == {"error": "RuntimeError"}

    def test_pending_approvals_degrades_independently(self):
        with patch(
            "src.routes.admin_wayz.list_providers",
            side_effect=RuntimeError("boom"),
        ):
            response = client.get("/admin/wayz/status")
        assert response.status_code == 200
        assert response.json()["data"]["pending_approvals"] == {"error": "RuntimeError"}

    def test_jobs_block_degrades_independently_and_never_500s(self):
        with patch(
            "src.routes.admin_wayz.get_job_runs",
            side_effect=RuntimeError("redis on fire"),
        ):
            response = client.get("/admin/wayz/status")
        assert response.status_code == 200
        assert response.json()["data"]["jobs"] == {"error": "RuntimeError"}
