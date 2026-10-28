"""The inference hot path must not repeat these lookups on every request."""

import asyncio
from unittest.mock import MagicMock, patch


def _admin_client(plan_type: str):
    client = MagicMock()
    chain = client.table.return_value.select.return_value.eq.return_value.eq.return_value
    chain.execute.return_value = MagicMock(data=[{"plans": {"plan_type": plan_type}}])
    return client


def test_admin_tier_is_cached():
    from src.db import plans

    client = _admin_client("admin")
    with patch.object(plans, "get_supabase_client", return_value=client):
        assert plans.is_admin_tier_user(7) is True
        assert plans.is_admin_tier_user(7) is True
    assert client.table.call_count == 1


def test_admin_tier_error_is_not_cached():
    from src.db import plans

    broken = MagicMock()
    broken.table.side_effect = RuntimeError("db down")
    with patch.object(plans, "get_supabase_client", return_value=broken):
        assert plans.is_admin_tier_user(8) is False
    client = _admin_client("admin")
    with patch.object(plans, "get_supabase_client", return_value=client):
        assert plans.is_admin_tier_user(8) is True


def test_global_ip_whitelist_is_cached_and_cleared_on_edit():
    from src.db import ip_whitelist

    client = MagicMock()
    q = client.table.return_value.select.return_value.eq.return_value.or_.return_value
    q.is_.return_value.execute.return_value = MagicMock(
        data=[{"ip_address": "10.0.0.0/8", "reason": "internal", "expires_at": None}]
    )
    with patch.object(ip_whitelist, "get_supabase_client", return_value=client):
        assert ip_whitelist.is_ip_whitelisted("10.1.2.3") is True
        assert ip_whitelist.is_ip_whitelisted("8.8.8.8") is False
        assert client.table.call_count == 1
        ip_whitelist.delete_whitelist_entry("00000000-0000-0000-0000-000000000000")
        assert ip_whitelist._global_entries_cache == {}


def test_expired_cached_entry_does_not_match():
    from src.db import ip_whitelist

    entries = [{"ip_address": "10.0.0.0/8", "reason": "x", "expires_at": "2000-01-01T00:00:00Z"}]
    import ipaddress

    assert ip_whitelist._matches_any(ipaddress.ip_address("10.1.2.3"), "10.1.2.3", entries) is False


def test_last_used_at_written_at_most_once_a_minute():
    from src.security import security

    client = MagicMock()
    key = {"id": 42, "is_active": True}
    with patch("src.services.job_usage.enforce_job_key"):
        security._validate_key_constraints(key, None, None, "api_keys_new", client)
        security._validate_key_constraints(key, None, None, "api_keys_new", client)
    assert client.table.return_value.update.call_count == 1


def test_api_key_id_is_cached():
    from src.utils import api_key_lookup

    with patch("src.db.api_keys.get_api_key_by_key", return_value={"id": 5}) as lookup:
        assert asyncio.run(api_key_lookup.get_api_key_id_with_retry("gw_live_x")) == 5
        assert asyncio.run(api_key_lookup.get_api_key_id_with_retry("gw_live_x")) == 5
    assert lookup.call_count == 1


def test_health_blob_read_once_per_ttl():
    from src.services.monitoring import health_routing

    with patch.object(
        health_routing.simple_health_cache, "get_models_health", return_value=[]
    ) as get:
        health_routing.is_model_healthy("m", "p")
        health_routing.is_model_healthy("m", "p")
    assert get.call_count == 1
