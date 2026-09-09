"""Tests for src/services/ops/job_runs.py -- the job-run registry backing
GET /admin/wayz/status's `jobs` block.
"""

import json
from unittest.mock import MagicMock, patch

from src.services.ops import job_runs


def setup_function(_fn):
    # Each test gets a clean in-process fallback store -- job_runs' module
    # state is otherwise shared across tests in this file.
    job_runs._fallback_store.clear()


class TestRecordJobRunWithRedis:
    @patch("src.services.ops.job_runs.get_redis_client")
    def test_writes_json_with_ttl(self, mock_get_client):
        mock_redis = MagicMock()
        mock_get_client.return_value = mock_redis

        job_runs.record_job_run(
            "model_sync", ok=True, summary={"models_synced": 5}, duration_ms=120
        )

        assert mock_redis.setex.call_count == 1
        key, ttl, value = mock_redis.setex.call_args[0]
        assert key == "ops:job:model_sync"
        assert ttl == 7 * 24 * 60 * 60
        record = json.loads(value)
        assert record["name"] == "model_sync"
        assert record["ok"] is True
        assert record["summary"] == {"models_synced": 5}
        assert record["duration_ms"] == 120
        assert record["ran_at"]  # ISO timestamp, non-empty

    @patch("src.services.ops.job_runs.get_redis_client")
    def test_records_failure_with_error(self, mock_get_client):
        mock_redis = MagicMock()
        mock_get_client.return_value = mock_redis

        job_runs.record_job_run("gpu_settlement", ok=False, error="boom")

        _key, _ttl, value = mock_redis.setex.call_args[0]
        record = json.loads(value)
        assert record["ok"] is False
        assert record["error"] == "boom"

    @patch("src.services.ops.job_runs.get_redis_client")
    def test_never_raises_when_redis_write_fails(self, mock_get_client):
        mock_redis = MagicMock()
        mock_redis.setex.side_effect = ConnectionError("redis down")
        mock_get_client.return_value = mock_redis

        # Should not raise -- falls back to the in-process store.
        job_runs.record_job_run("pricing_drift", ok=True)

        assert job_runs._fallback_store["pricing_drift"]["ok"] is True

    @patch("src.services.ops.job_runs.get_redis_client")
    def test_never_raises_when_get_redis_client_raises(self, mock_get_client):
        mock_get_client.side_effect = RuntimeError("no redis configured")

        job_runs.record_job_run("gpu_rollup", ok=True, summary={"hour": "x"})

        assert job_runs._fallback_store["gpu_rollup"]["summary"] == {"hour": "x"}


class TestRecordJobRunFallback:
    @patch("src.services.ops.job_runs.get_redis_client", return_value=None)
    def test_uses_in_process_store_when_redis_unavailable(self, _mock_get_client):
        job_runs.record_job_run("wayz_staking_sync", ok=True, summary={"wallets_synced": 3})

        record = job_runs._fallback_store["wayz_staking_sync"]
        assert record["ok"] is True
        assert record["summary"] == {"wallets_synced": 3}


class TestGetJobRuns:
    @patch("src.services.ops.job_runs.get_redis_client")
    def test_reads_from_redis(self, mock_get_client):
        mock_redis = MagicMock()
        stored = {
            "name": "model_sync",
            "ok": True,
            "ran_at": "2026-09-09T00:00:00+00:00",
            "duration_ms": 500,
            "summary": {"models_synced": 10},
            "error": None,
        }
        mock_redis.get.return_value = json.dumps(stored)
        mock_get_client.return_value = mock_redis

        result = job_runs.get_job_runs(["model_sync"])

        assert result["model_sync"] == stored
        mock_redis.get.assert_called_once_with("ops:job:model_sync")

    @patch("src.services.ops.job_runs.get_redis_client", return_value=None)
    def test_falls_back_to_in_process_store(self, _mock_get_client):
        job_runs._fallback_store["gpu_liveness_sweep"] = {"name": "gpu_liveness_sweep", "ok": True}

        result = job_runs.get_job_runs(["gpu_liveness_sweep", "never_ran_job"])

        assert result["gpu_liveness_sweep"]["ok"] is True
        assert result["never_ran_job"] is None

    @patch("src.services.ops.job_runs.get_redis_client")
    def test_missing_job_returns_none(self, mock_get_client):
        mock_redis = MagicMock()
        mock_redis.get.return_value = None
        mock_get_client.return_value = mock_redis

        result = job_runs.get_job_runs(["never_ran"])

        assert result == {"never_ran": None}

    @patch("src.services.ops.job_runs.get_redis_client")
    def test_never_raises_on_redis_read_error(self, mock_get_client):
        mock_redis = MagicMock()
        mock_redis.get.side_effect = ConnectionError("redis down")
        mock_get_client.return_value = mock_redis

        result = job_runs.get_job_runs(["model_sync"])

        assert result == {"model_sync": None}

    @patch("src.services.ops.job_runs.get_redis_client")
    def test_never_raises_on_malformed_json(self, mock_get_client):
        mock_redis = MagicMock()
        mock_redis.get.return_value = "{not valid json"
        mock_get_client.return_value = mock_redis

        result = job_runs.get_job_runs(["model_sync"])

        assert result == {"model_sync": None}
