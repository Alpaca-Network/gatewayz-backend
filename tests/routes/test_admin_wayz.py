"""Tests for GET /admin/wayz/status (src/routes/admin_wayz.py)."""

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

import src.routes.admin_wayz as admin_wayz
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

    def test_job_run_within_interval_is_not_stale(self):
        """Positive counterpart to test_stale_job_flagged_...: a job that
        ran recently (well inside 2x its interval) must NOT be flagged
        stale."""
        from datetime import UTC, datetime, timedelta

        fresh_ran_at = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
        fake_record = {
            "name": "gpu_liveness_sweep",
            "ok": True,
            "ran_at": fresh_ran_at,
            "duration_ms": 5,
            "summary": {},
            "error": None,
        }
        with patch(
            "src.routes.admin_wayz.get_job_runs",
            return_value={"gpu_liveness_sweep": fake_record},
        ):
            response = client.get("/admin/wayz/status")
        job = response.json()["data"]["jobs"]["gpu_liveness_sweep"]
        assert job["stale"] is False
        assert job["ok"] is True


class TestStakingBlockRpc:
    """The staking block's RPC latest-block lookup (_rpc_latest_block) and
    the lag_blocks arithmetic derived from it -- see PR #2299 review round 1.
    """

    def setup_method(self, _method):
        _override_admin()

    def teardown_method(self, _method):
        _clear_override()

    def test_rpc_success_computes_lag_blocks(self):
        # web3 7.6.0 API: Web3(Web3.HTTPProvider(url, request_kwargs=...)).eth.block_number
        mock_w3_instance = MagicMock()
        mock_w3_instance.eth.block_number = 142
        with (
            patch("src.routes.admin_wayz.get_stake_totals", return_value=("1000", 2)),
            patch(
                "src.routes.admin_wayz.get_sync_cursor_row",
                return_value={"last_synced_block": 100, "updated_at": "2026-09-09T00:00:00+00:00"},
            ),
            patch.object(admin_wayz.Config, "WAYZ_STAKING_CONTRACT_ADDRESS", "0xStakingContract"),
            patch("web3.Web3") as MockWeb3,
        ):
            MockWeb3.return_value = mock_w3_instance
            block = admin_wayz._build_staking_block()

        assert block["rpc_latest_block"] == 142
        assert block["cursor_block"] == 100
        assert block["lag_blocks"] == 42

    def test_rpc_timeout_degrades_to_null_without_failing_staking_block(self):
        with (
            patch("src.routes.admin_wayz.get_stake_totals", return_value=("1000", 2)),
            patch(
                "src.routes.admin_wayz.get_sync_cursor_row",
                return_value={"last_synced_block": 100, "updated_at": "2026-09-09T00:00:00+00:00"},
            ),
            patch.object(admin_wayz.Config, "WAYZ_STAKING_CONTRACT_ADDRESS", "0xStakingContract"),
            patch("web3.Web3", side_effect=TimeoutError("RPC timed out")),
        ):
            block = admin_wayz._build_staking_block()

        assert block["rpc_latest_block"] is None
        assert block["lag_blocks"] is None
        # The rest of the block is still populated -- one broken RPC call
        # must not blank out cursor/wallet data that has nothing to do with it.
        assert block["cursor_block"] == 100
        assert block["wallets"] == 2
        assert block["total_staked_wei"] == "1000"

    def test_rpc_generic_exception_degrades_to_null(self):
        with (
            patch("src.routes.admin_wayz.get_stake_totals", return_value=("1000", 2)),
            patch(
                "src.routes.admin_wayz.get_sync_cursor_row",
                return_value={"last_synced_block": 100, "updated_at": "2026-09-09T00:00:00+00:00"},
            ),
            patch.object(admin_wayz.Config, "WAYZ_STAKING_CONTRACT_ADDRESS", "0xStakingContract"),
            patch("web3.Web3", side_effect=ConnectionError("rpc unreachable")),
        ):
            block = admin_wayz._build_staking_block()

        assert block["rpc_latest_block"] is None
        assert block["lag_blocks"] is None

    def test_lag_blocks_is_none_when_cursor_never_synced(self):
        """cursor_block is None (never synced) -- lag_blocks must stay None
        even when the RPC call itself succeeds, per _build_staking_block's
        `if cursor_block is not None` guard."""
        mock_w3_instance = MagicMock()
        mock_w3_instance.eth.block_number = 999
        with (
            patch("src.routes.admin_wayz.get_stake_totals", return_value=("0", 0)),
            patch("src.routes.admin_wayz.get_sync_cursor_row", return_value=None),
            patch.object(admin_wayz.Config, "WAYZ_STAKING_CONTRACT_ADDRESS", "0xStakingContract"),
            patch("web3.Web3") as MockWeb3,
        ):
            MockWeb3.return_value = mock_w3_instance
            block = admin_wayz._build_staking_block()

        assert block["cursor_block"] is None
        assert block["rpc_latest_block"] == 999
        assert block["lag_blocks"] is None

    def test_rpc_never_called_when_staking_contract_unconfigured(self):
        with (
            patch("src.routes.admin_wayz.get_stake_totals", return_value=("0", 0)),
            patch("src.routes.admin_wayz.get_sync_cursor_row") as mock_cursor,
            patch.object(admin_wayz.Config, "WAYZ_STAKING_CONTRACT_ADDRESS", None),
            patch("web3.Web3") as MockWeb3,
        ):
            block = admin_wayz._build_staking_block()

        mock_cursor.assert_not_called()
        MockWeb3.assert_not_called()
        assert block["rpc_latest_block"] is None
        assert block["lag_blocks"] is None
        assert block["cursor_block"] is None
