"""Default per-key limits must not throttle normal use.

The original 1,000 requests / 1M tokens per hour meant ~16 requests and ~16.7k
tokens a minute; a user hit 429 after one request (production, 2026-10-27).
"""

from src.db import rate_limit_defaults as d


def test_new_keys_get_at_least_600_requests_a_minute():
    cfg = d.default_rate_limit_config(42)
    assert cfg["api_key_id"] == 42
    assert cfg["window_size"] == 3600
    assert cfg["max_requests"] // 60 >= 600
    assert cfg["max_tokens"] // 60 >= 500_000
    assert cfg["burst_limit"] >= 600
    assert cfg["concurrency_limit"] >= 50


def test_explicit_max_requests_is_kept():
    assert d.default_rate_limit_config(1, max_requests=50)["max_requests"] == 50


def test_migration_raises_only_untouched_default_rows():
    from pathlib import Path

    sql = Path("supabase/migrations/20261027170000_raise_default_key_rate_limits.sql").read_text()
    assert "where max_requests = 1000" in sql
    assert "and max_tokens = 1000000" in sql
    assert f"max_requests = {d.DEFAULT_KEY_MAX_REQUESTS_PER_HOUR}" in sql
