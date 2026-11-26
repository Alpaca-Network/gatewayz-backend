"""record_attestation can never overwrite a 'restricted' status, even when
mark_restricted runs between its read and its write (race)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.db import wallet_eligibility as we


def _client():
    client = MagicMock()
    table = client.table.return_value
    table.update.return_value.eq.return_value.neq.return_value.execute.return_value = None
    table.upsert.return_value.execute.return_value = None
    return client, table


def test_an_existing_row_is_updated_only_where_it_is_not_restricted():
    client, table = _client()
    rows = [{"user_id": 1, "status": "none"}, {"user_id": 1, "status": "eligible"}]
    with (
        patch.object(we, "get_supabase_client", return_value=client),
        patch.object(we, "get_eligibility", side_effect=rows),
    ):
        result = we.record_attestation(1, "v1", None)
    table.update.return_value.eq.return_value.neq.assert_called_once_with("status", "restricted")
    table.upsert.assert_not_called()
    assert result == rows[1]


def test_a_new_row_is_inserted_without_overwriting_one_that_appeared_meanwhile():
    client, table = _client()
    restricted = {"user_id": 1, "status": "restricted"}
    with (
        patch.object(we, "get_supabase_client", return_value=client),
        patch.object(we, "get_eligibility", side_effect=[{}, restricted]),
    ):
        result = we.record_attestation(1, "v1", None)
    assert table.upsert.call_args.kwargs["ignore_duplicates"] is True
    # The caller sees what is stored: the restriction stands.
    assert result == restricted


def test_a_restricted_row_is_returned_untouched():
    client, table = _client()
    restricted = {"user_id": 1, "status": "restricted"}
    with (
        patch.object(we, "get_supabase_client", return_value=client),
        patch.object(we, "get_eligibility", return_value=restricted),
    ):
        assert we.record_attestation(1, "v1", None) == restricted
    table.update.assert_not_called()
    table.upsert.assert_not_called()
