"""Tests for usage_records plaintext-key hardening (gatewayz-backend#2258,
threat model docs/security/ANONYMITY_THREAT_MODEL.md L9).

record_usage() must stop persisting the plaintext api_key column and write
api_key_id + api_key_last4 instead. Readers that used to filter/join on the
plaintext api_key column (src/db/rate_limits.py) must use api_key_id instead,
with unchanged externally-visible behaviour for the same key.
"""

from unittest.mock import MagicMock, Mock, patch

import pytest

from src.db.rate_limits import get_rate_limit_usage_stats, get_system_rate_limit_stats
from src.db.users import record_usage


@pytest.fixture
def sb():
    """In-memory Supabase stub (named `sb` per conftest convention -- tests using
    it are exempt from the database-availability skip)."""
    return None


def _mock_table_client(table_data: dict):
    """table_data maps table name -> the .data a chained query call returns."""
    queries: dict = {}

    def make_query(name):
        if name not in queries:
            query = MagicMock()
            query.select.return_value = query
            query.insert.return_value = query
            query.eq.return_value = query
            query.gte.return_value = query
            query.lt.return_value = query
            query.limit.return_value = query
            query.execute.return_value = MagicMock(data=table_data.get(name, []))
            queries[name] = query
        return queries[name]

    client = MagicMock()
    client.table.side_effect = make_query
    return client, queries


# ============================================================
# record_usage -- writer
# ============================================================


class TestRecordUsageNoPlaintextKey:
    def test_never_writes_plaintext_api_key(self, sb):
        client, queries = _mock_table_client({})
        with (
            patch("src.db.users.get_supabase_client", return_value=client),
            patch(
                "src.db.users.get_api_key_by_key",
                return_value={"id": 4242, "api_key": "gw_live_abcd1234"},
            ),
        ):
            record_usage(
                user_id=1,
                api_key="gw_live_abcd1234",
                model="gpt-test",
                tokens_used=100,
                cost=0.01,
            )

        insert_call = queries["usage_records"].insert
        assert insert_call.called
        payload = insert_call.call_args[0][0]

        assert payload["api_key"] is None
        assert "gw_live_abcd1234" not in str(payload)
        assert payload["api_key_id"] == 4242
        assert payload["api_key_last4"] == "1234"
        assert payload["user_id"] == 1
        assert payload["model"] == "gpt-test"
        assert payload["tokens_used"] == 100
        assert payload["cost"] == 0.01

    def test_unresolvable_key_writes_null_api_key_id_but_still_last4(self, sb):
        client, queries = _mock_table_client({})
        with (
            patch("src.db.users.get_supabase_client", return_value=client),
            patch("src.db.users.get_api_key_by_key", return_value=None),
        ):
            record_usage(
                user_id=1,
                api_key="gw_live_unknownkey",
                model="gpt-test",
                tokens_used=10,
                cost=0.0,
            )

        payload = queries["usage_records"].insert.call_args[0][0]
        assert payload["api_key"] is None
        assert payload["api_key_id"] is None
        assert payload["api_key_last4"] == "nkey"

    def test_lookup_failure_does_not_raise_or_leak_key(self, sb):
        client, queries = _mock_table_client({})
        with (
            patch("src.db.users.get_supabase_client", return_value=client),
            patch(
                "src.db.users.get_api_key_by_key",
                side_effect=RuntimeError("db unavailable"),
            ),
        ):
            # Must not raise -- record_usage swallows failures so billing/inference
            # flow is never broken by a usage-recording problem.
            record_usage(
                user_id=1,
                api_key="gw_live_abcd1234",
                model="gpt-test",
                tokens_used=10,
                cost=0.0,
            )

        payload = queries["usage_records"].insert.call_args[0][0]
        assert payload["api_key"] is None
        assert payload["api_key_id"] is None

    def test_no_api_key_no_lookup(self, sb):
        client, queries = _mock_table_client({})
        lookup = MagicMock()
        with (
            patch("src.db.users.get_supabase_client", return_value=client),
            patch("src.db.users.get_api_key_by_key", lookup),
        ):
            record_usage(user_id=1, api_key="", model="gpt-test", tokens_used=1, cost=0.0)

        lookup.assert_not_called()
        payload = queries["usage_records"].insert.call_args[0][0]
        assert payload["api_key_id"] is None
        assert payload["api_key_last4"] is None


# ============================================================
# rate_limits readers -- must key off api_key_id, not plaintext api_key
# ============================================================


class TestRateLimitReadersUseApiKeyId:
    def test_usage_stats_filters_by_api_key_id(self, sb):
        client, queries = _mock_table_client(
            {"usage_records": [{"tokens_used": 50, "created_at": "2026-09-03T00:00:30Z"}]}
        )
        with (
            patch("src.db.rate_limits.get_supabase_client", return_value=client),
            patch(
                "src.db.rate_limits.get_api_key_by_key",
                return_value={"id": 77, "api_key": "gw_live_x"},
            ),
        ):
            result = get_rate_limit_usage_stats("gw_live_x", "minute")

        usage_query = queries["usage_records"]
        # Filtered by the resolved id, never the plaintext key.
        usage_query.eq.assert_any_call("api_key_id", 77)
        assert result["total_requests"] == 1
        assert result["total_tokens"] == 50

    def test_usage_stats_unresolvable_key_returns_zero_without_querying_table(self, sb):
        client, _queries = _mock_table_client({"usage_records": [{"tokens_used": 999}]})
        with (
            patch("src.db.rate_limits.get_supabase_client", return_value=client),
            patch("src.db.rate_limits.get_api_key_by_key", return_value=None),
        ):
            result = get_rate_limit_usage_stats("gw_live_unknown", "minute")

        assert result["total_requests"] == 0
        assert result["total_tokens"] == 0
        # An unresolvable key has no rows to find -- never queries the table at all
        # (would otherwise risk passing api_key_id=None into .eq()).
        client.table.assert_not_called()

    def test_system_stats_counts_unique_api_key_ids(self, sb):
        client, queries = _mock_table_client(
            {
                "usage_records": [
                    {"api_key_id": 1, "tokens_used": 10},
                    {"api_key_id": 1, "tokens_used": 5},
                    {"api_key_id": 2, "tokens_used": 20},
                    {"api_key_id": None, "tokens_used": 3},  # legacy row, pre-migration
                ]
            }
        )
        with patch("src.db.rate_limits.get_supabase_client", return_value=client):
            result = get_system_rate_limit_stats()

        # 2 distinct real api_key_id values; the legacy None row doesn't collapse
        # into a phantom "active key".
        assert result["minute"]["active_keys"] == 2
        assert result["minute"]["tokens"] == 38
        usage_query = queries["usage_records"]
        usage_query.select.assert_any_call("api_key_id, tokens_used")
