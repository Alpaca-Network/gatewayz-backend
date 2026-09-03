"""Tests for src.db.gpu_work (gatewayz-backend#2262 #2265)."""

from unittest.mock import MagicMock, patch

import pytest

from src.db.gpu_work import mark_attested, record_work


@pytest.fixture
def sb():
    # Presence of this fixture name tells tests/conftest.py's autouse
    # skip_if_no_database to treat this file as using an in-memory stub
    # rather than a real DB connection (see tests/db/test_faucet.py).
    return None


def _mock_table_client(
    table_data: dict, raise_on_insert: bool = False, raise_on_update: bool = False
):
    queries: dict = {}

    def make_query(name):
        if name not in queries:
            query = MagicMock()
            query.select.return_value = query
            query.eq.return_value = query
            query.update.return_value = query
            if raise_on_insert:
                query.insert.side_effect = RuntimeError("duplicate key value")
            else:
                query.insert.return_value = query
            if raise_on_update:
                query.update.side_effect = RuntimeError("boom")
            query.execute.return_value = MagicMock(data=table_data.get(name, []))
            queries[name] = query
        return queries[name]

    client = MagicMock()
    client.table.side_effect = make_query
    return client


def test_record_work_inserts_row_no_content(sb):
    row = {"id": 1, "billing_ref": "ref-1"}
    client = _mock_table_client({"provider_work": [row]})
    with patch("src.db.gpu_work.get_supabase_client", return_value=client):
        result = record_work(
            billing_ref="ref-1",
            node_id=7,
            provider_id=3,
            model="llama-3.1-8b-instruct",
            prompt_hash="hash-p",
            response_hash="hash-r",
            prompt_tokens=10,
            completion_tokens=20,
            latency_ms=123,
            status="completed",
        )
    assert result == row
    # The insert payload must never carry prompt/response content (threat model G3).
    inserted = client.table("provider_work").insert.call_args[0][0]
    assert "prompt" not in inserted
    assert "messages" not in inserted
    assert inserted["prompt_hash"] == "hash-p"
    assert inserted["response_hash"] == "hash-r"


def test_record_work_without_billing_ref_returns_none_and_skips_insert(sb):
    client = _mock_table_client({"provider_work": [{"id": 1}]})
    with patch("src.db.gpu_work.get_supabase_client", return_value=client):
        result = record_work(
            billing_ref=None,
            node_id=7,
            provider_id=3,
            model="m",
            prompt_hash="h",
            response_hash="h",
            prompt_tokens=1,
            completion_tokens=1,
            latency_ms=1,
            status="completed",
        )
    assert result is None
    client.table.assert_not_called()


def test_record_work_duplicate_billing_ref_returns_none(sb):
    client = _mock_table_client({}, raise_on_insert=True)
    with patch("src.db.gpu_work.get_supabase_client", return_value=client):
        result = record_work(
            billing_ref="dup",
            node_id=1,
            provider_id=1,
            model="m",
            prompt_hash="h",
            response_hash="h",
            prompt_tokens=1,
            completion_tokens=1,
            latency_ms=1,
            status="failed",
        )
    assert result is None


def test_mark_attested_updates_row(sb):
    client = _mock_table_client({"provider_work": [{"id": 1, "attested": True}]})
    with patch("src.db.gpu_work.get_supabase_client", return_value=client):
        assert mark_attested(1, "0xsig") is True
    update_payload = client.table("provider_work").update.call_args[0][0]
    assert update_payload == {"attested": True, "attestation_sig": "0xsig"}


def test_mark_attested_returns_false_on_db_error(sb):
    client = _mock_table_client({}, raise_on_update=True)
    with patch("src.db.gpu_work.get_supabase_client", return_value=client):
        assert mark_attested(1, "0xsig") is False
