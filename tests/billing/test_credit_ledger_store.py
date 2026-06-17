"""Tests for the Phase 3 credit-ledger shadow dual-write store.

Covers the pure entry-builder and the non-blocking, idempotent shadow recorder
(Supabase client mocked). The shadow path must NEVER raise and must skip when a
ref already has rows.
"""

import asyncio
from decimal import Decimal
from unittest.mock import MagicMock, patch

from src.services.credit_ledger import ALLOWANCE, PURCHASED, REVENUE, EntryState, is_balanced
from src.services.billing.credit_ledger_store import (
    build_settled_entries,
    record_shadow_settlement,
)


# --------------------------------------------------------------------------- #
# build_settled_entries — pure double-entry math
# --------------------------------------------------------------------------- #
def test_build_settled_entries_allowance_only():
    entries = build_settled_entries("req-1", cost="0.50", allowance="2.00", purchased="0")
    accounts = {e.account: e for e in entries}
    assert accounts[ALLOWANCE].debit == Decimal("0.50")
    assert PURCHASED not in accounts  # no purchased line when allowance covers it
    assert accounts[REVENUE].credit == Decimal("0.50")
    assert all(e.state is EntryState.SETTLED for e in entries)
    assert is_balanced(entries)


def test_build_settled_entries_spans_allowance_and_purchased():
    entries = build_settled_entries("req-2", cost="3.00", allowance="2.00", purchased="5.00")
    accounts = {e.account: e for e in entries}
    assert accounts[ALLOWANCE].debit == Decimal("2.00")
    assert accounts[PURCHASED].debit == Decimal("1.00")  # remainder from purchased
    assert accounts[REVENUE].credit == Decimal("3.00")
    assert is_balanced(entries)


def test_build_settled_entries_zero_cost_is_empty():
    assert build_settled_entries("req-3", cost="0", allowance="2.00", purchased="0") == []


# --------------------------------------------------------------------------- #
# record_shadow_settlement — non-blocking, idempotent persistence
# --------------------------------------------------------------------------- #
def _mock_client(existing_data=None, insert_raises=False):
    client = MagicMock()
    table = client.table.return_value
    table.select.return_value.eq.return_value.limit.return_value.execute.return_value = MagicMock(
        data=existing_data
    )
    if insert_raises:
        table.insert.side_effect = RuntimeError("supabase down")
    else:
        table.insert.return_value.execute.return_value = MagicMock(data=[{"id": 1}])
    return client


def test_record_shadow_settlement_writes_rows():
    client = _mock_client(existing_data=[])
    with patch("src.config.supabase_config.get_supabase_client", return_value=client):
        ok = asyncio.run(
            record_shadow_settlement(
                ref="req-10", user_id=7, cost="1.25", allowance="5.00", purchased="0"
            )
        )
    assert ok is True
    # one insert of the settled rows (allowance debit + revenue credit)
    inserted = client.table.return_value.insert.call_args.args[0]
    assert {r["account"] for r in inserted} == {ALLOWANCE, REVENUE}
    assert all(r["state"] == "settled" and r["user_id"] == 7 and r["ref"] == "req-10" for r in inserted)


def test_record_shadow_settlement_idempotent_skips_existing_ref():
    client = _mock_client(existing_data=[{"id": 99}])  # ref already recorded
    with patch("src.config.supabase_config.get_supabase_client", return_value=client):
        ok = asyncio.run(
            record_shadow_settlement(
                ref="req-dup", user_id=7, cost="1.00", allowance="5.00", purchased="0"
            )
        )
    assert ok is False
    client.table.return_value.insert.assert_not_called()


def test_record_shadow_settlement_non_blocking_on_error():
    client = _mock_client(existing_data=[], insert_raises=True)
    with patch("src.config.supabase_config.get_supabase_client", return_value=client):
        # must NOT raise — billing path depends on this
        ok = asyncio.run(
            record_shadow_settlement(
                ref="req-err", user_id=7, cost="1.00", allowance="5.00", purchased="0"
            )
        )
    assert ok is False


def test_record_shadow_settlement_empty_ref_is_noop():
    with patch("src.config.supabase_config.get_supabase_client") as get_client:
        ok = asyncio.run(
            record_shadow_settlement(ref="", user_id=7, cost="1.00", allowance="5.00", purchased="0")
        )
    assert ok is False
    get_client.assert_not_called()  # bails before touching the DB


def test_record_shadow_settlement_oversized_cost_is_noop():
    # cost exceeds allowance+purchased -> split_charge raises InsufficientBalance,
    # which the shadow path must swallow (return False, never raise, never insert).
    client = _mock_client(existing_data=[])
    with patch("src.config.supabase_config.get_supabase_client", return_value=client):
        ok = asyncio.run(
            record_shadow_settlement(
                ref="req-over", user_id=7, cost="10.00", allowance="1.00", purchased="0.50"
            )
        )
    assert ok is False
    client.table.return_value.insert.assert_not_called()
